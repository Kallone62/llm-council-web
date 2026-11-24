"""REST/SSE and persistence contracts for empty Chairman synthesis."""
import asyncio
import copy
import json

import pytest
from fastapi.testclient import TestClient

from backend import conversation_context, engine_runtime, main, storage


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("answer", ["", " \n\t"])
def test_empty_chairman_is_not_a_successful_council_result(tmp_path, monkeypatch, stream, cached, answer):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    stage1 = [{"model": "provider/a", "response": "A"}, {"model": "provider/b", "response": "B"}]
    stage2 = [{"model": "provider/a", "ranking": "review", "parsed_ranking": {"ranking": ["Response B", "Response A"]}}]
    raw = {"model": "provider/chairman", "response": answer}
    metadata = {
        "status": "success",
        "quality_metrics": {"core": {"synthesis_attribution": {"grounded": True}}},
        "verdict": {"verdict_type": "binary", "verdict": "approved", "confidence": 0.99},
        "aggregate_rankings": [{"model": "provider/a", "average_position": 1.0},
                               {"model": "provider/b", "average_position": 2.0}],
        "usage": {"total": {"total_tokens": 17}},
    }
    original_metadata = copy.deepcopy(metadata)

    async def native_stage3():
        return raw, {"total_tokens": 17}, None

    async def fake_native(_query, **kwargs):
        final = raw
        if not cached:
            final, _, _ = await engine_runtime.wrap_stage("stage3", native_stage3)()
        return stage1, stage2, final, metadata

    async def fake_title(_query):
        return "Empty Chairman test"

    def forbidden(**kwargs):
        pytest.fail("Invalid synthesis must not be scored at the app boundary")

    monkeypatch.setattr(main, "run_full_council", fake_native)
    monkeypatch.setattr(main, "generate_conversation_title", fake_title)
    monkeypatch.setattr(engine_runtime, "calculate_quality_metrics", forbidden)
    client = TestClient(main.app)
    conversation = client.post("/api/conversations", json={}).json()
    endpoint = f"/api/conversations/{conversation['id']}/message"
    response = client.post(endpoint + ("/stream" if stream else ""), json={"content": "Should we ship?"})
    assert response.status_code == 200
    if stream:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        final = next(e for e in events if e["type"] == "stage3_complete")
        complete = next(e for e in events if e["type"] == "complete")
        result = final["data"]
        meta = complete["metadata"]
        assert final["metadata"] == meta
        assert next(e["data"] for e in events if e["type"] == "stage1_complete") == stage1
        assert next(e["data"] for e in events if e["type"] == "stage2_complete") == stage2
        progress = [e["data"] for e in events if e["type"] == "engine_progress"]
        assert [(e["stage"], e["state"]) for e in progress] == (
            [] if cached else [("stage3", "started"), ("stage3", "failed")]
        )
        if not cached:
            assert progress[-1]["error_status"] == "empty_synthesis"
        assert meta["ui_runtime_progress"] == progress
    else:
        payload = response.json()
        result, meta = payload["stage3"], payload["metadata"]
        assert payload["stage1"] == stage1
        assert payload["stage2"] == stage2

    assert result["error_status"] == "empty_synthesis"
    assert result["model"] == raw["model"]
    assert result["response"].strip()
    assert meta["status"] == "failed"
    assert meta["quality_metrics"] is None
    assert meta.get("verdict") is None
    assert meta["usage"] == original_metadata["usage"]
    assert meta["aggregate_rankings"] == original_metadata["aggregate_rankings"]
    stored = storage.get_conversation(conversation["id"])
    assert stored["messages"][-1]["stage3"] == result
    assert stored["messages"][-1]["stage1"] == stage1
    assert stored["messages"][-1]["stage2"] == stage2
    assert stored["messages"][-1]["metadata"] == meta
    query, memory = asyncio.run(conversation_context.prepare_council_query(stored, "Next question"))
    assert query == "Next question"
    assert memory is None
    assert raw["response"] == answer
    assert metadata == original_metadata
