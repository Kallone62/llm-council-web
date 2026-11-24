"""Stage 2 status must survive the actual REST/SSE and storage boundaries."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from backend import engine_runtime, main, storage


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("scenario,state,live_state", [
    ("empty", "failed", "failed"),
    ("invalid", "failed", "failed"),
    ("abstained", "failed", "failed"),
    ("self_only", "failed", "partial"),
    ("partial", "partial", "partial"),
    ("complete", "completed", "completed"),
    ("cached_empty", "failed", None),
    ("single", "skipped", None),
])
def test_peer_review_status_is_persisted(tmp_path, monkeypatch, stream, scenario, state, live_state):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    monkeypatch.setattr(engine_runtime.council, "_get_exclude_self_votes", lambda: True)
    letters = "A" if scenario == "single" else "ABC"
    labels = {f"Response {letter}": {"model": f"provider/{letter.lower()}", "display_index": i}
              for i, letter in enumerate(letters)}
    stage1 = [{"model": item["model"], "response": "candidate answer"} for item in labels.values()]

    def ballot(model, ranking, **extra):
        return {"model": model, "ranking": "raw critique", "parsed_ranking": {"ranking": ranking, **extra}}

    reviews = {
        "invalid": [ballot("provider/a", [])],
        "abstained": [ballot("provider/a", [], abstained=True)],
        "self_only": [ballot("provider/a", ["Response A"])],
        "partial": [ballot("provider/a", ["Response B", "Response C"])],
        "complete": [ballot(item["model"], list(labels)) for item in labels.values()],
    }.get(scenario, [])
    aggregate = engine_runtime.council.calculate_aggregate_rankings(reviews, labels)
    final = {"model": "provider/chairman", "response": "Chairman synthesis"}
    metadata = {
        "label_to_model": labels, "aggregate_rankings": aggregate,
        "verdict": {"verdict": "approved"}, "status": "success",
        "usage": {"total": {"total_tokens": 17}},
    }
    if state in ("failed", "skipped"):
        metadata["quality_metrics"] = {"old": True}
    if scenario == "single":
        metadata["degraded_mode"] = "single_model"
    original = copy.deepcopy(metadata)

    async def native_stage2(*args, **kwargs):
        return reviews, labels, {"total_tokens": 17}

    async def native(_query, **kwargs):
        if live_state is not None:
            result = await engine_runtime.wrap_stage("stage2", native_stage2)("question", stage1)
            assert result[0] is reviews
        return stage1, reviews, final, metadata

    async def title(_query):
        return "Review status test"

    monkeypatch.setattr(main, "run_full_council", native)
    monkeypatch.setattr(main, "generate_conversation_title", title)
    client = TestClient(main.app)
    conversation = client.post("/api/conversations", json={}).json()
    endpoint = f"/api/conversations/{conversation['id']}/message"
    response = client.post(endpoint + ("/stream" if stream else ""), json={"content": "Decide"})
    assert response.status_code == 200
    if stream:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        review_event = next(e for e in events if e["type"] == "stage2_complete")
        meta = next(e["metadata"] for e in events if e["type"] == "complete")
        assert review_event["metadata"] == meta
        assert review_event["data"] == reviews
        assert next(e["data"] for e in events if e["type"] == "stage3_complete") == final
        progress = [e["data"]["state"] for e in events
                    if e["type"] == "engine_progress" and e["data"]["stage"] == "stage2"]
        assert progress == (["started", live_state] if live_state is not None else [])
    else:
        payload = response.json()
        meta = payload["metadata"]
        assert payload["stage1"] == stage1
        assert payload["stage2"] == reviews
        assert payload["stage3"] == final

    assert meta["peer_review"]["state"] == state
    assert meta["peer_review"]["effective_votes"] == sum(item["vote_count"] for item in aggregate)
    assert meta["aggregate_rankings"] == aggregate
    assert meta["usage"] == original["usage"]
    assert meta["verdict"] == original["verdict"]
    assert meta["status"] == ("partial" if state in ("failed", "partial") else "success")
    if state in ("failed", "skipped"):
        assert meta["quality_metrics"] is None
    saved = storage.get_conversation(conversation["id"])["messages"][-1]
    assert saved["stage2"] == reviews and saved["stage3"] == final
    assert saved["metadata"] == meta
    assert metadata == original
