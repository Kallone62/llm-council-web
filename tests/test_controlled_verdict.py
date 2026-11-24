"""Native verdict/dissent acceptance through real HTTP, not mocked verdict results."""
import json
import re
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient
from llm_council import council

from test_controlled_profiles import (
    KEY, PROFILES, TITLE, TITLE_MODEL, decode_response, deny_external_network, isolated_profile_config,
)

MODELS, CHAIRMAN, _, _ = PROFILES["high"]
QUESTION = "ENG14: Should the archive be retained before migration?"
SYNTHESIS = "Retain the archive until integrity checks complete."
MINORITY = MODELS[-1]
LABELS = [f"Response {letter}" for letter in "ABCD"]
DISSENT = f"Minority perspective: {MINORITY} scored significantly lower (gave 1.0, median was 9.0)"


def candidate(model):
    return f"Archive candidate {MODELS.index(model) + 1}: retain the backup."


def install_verdict_provider(monkeypatch, requested, tied, outlier):
    calls, ballots, prompts, mapping = [], {}, [], {}
    effective = "tie_breaker" if requested == "tie_breaker" or (requested == "binary" and tied) else requested
    verdict = {"verdict": "rejected", "confidence": 0.83,
               "rationale": "Integrity has not been established; retain the archive."}
    if effective == "tie_breaker":
        verdict["deadlock_resolution"] = "The integrity concern outweighs convenience."
    final = SYNTHESIS if requested == "synthesis" else json.dumps(verdict)

    deny_external_network(monkeypatch, "Unexpected external network in verdict acceptance")

    async def handle(_transport, request):
        assert request.method == "POST"
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers.get("authorization") == f"Bearer {KEY}"
        body = json.loads(request.content)
        model, prompt = body["model"], body["messages"][-1]["content"]
        assert QUESTION in prompt
        if prompt.startswith("Generate a very short title"):
            stage, content = "title", TITLE
            assert model == TITLE_MODEL
        elif prompt == QUESTION:
            stage, content = "stage1", candidate(model)
            assert model in MODELS
        elif "<responses_to_evaluate>" in prompt:
            stage = "stage2"
            assert model in MODELS
            observed = {f"Response {label}": MODELS[int(index) - 1] for label, index in re.findall(
                r'<candidate_response id="([A-D])">\nArchive candidate ([1-4]): retain the backup\.\n</candidate_response>', prompt)}
            assert set(observed) == set(LABELS) and set(observed.values()) == set(MODELS)
            if mapping:
                assert mapping == observed
            mapping.update(observed)
            own = next(label for label, author in mapping.items() if author == model)
            offset = LABELS.index(own) if tied else 0
            ranking = LABELS[offset:] + LABELS[:offset]
            # Cyclic ranks put every model first on its own ballot, and once
            # each in positions 2, 3, 4 on the other three ballots. Equal Borda
            # scores are therefore guaranteed without fixing native label shuffle.
            scores = dict.fromkeys(LABELS, 9)
            if outlier and model == MINORITY:
                scores[ranking[-1]] = 1
            critique = f"{model}: " + ("Integrity concern requires independent verification." if model == MINORITY else "Archive retention is prudent.")
            ballot = {"ranking": ranking, "scores": scores}
            content = critique + "\n```json\n" + json.dumps(ballot) + "\n```"
            ballots[model] = (ballot, content)
        else:
            stage, content = "stage3", final
            assert model == CHAIRMAN
            assert len(ballots) == 4
            assert all(raw in prompt for _, raw in ballots.values())
            assert "AGGREGATE RANKINGS" in prompt
            if effective == "tie_breaker":
                assert prompt.startswith("You are the Chairman resolving a DEADLOCKED deliberation.")
                assert "DECIDING VOTE" in prompt and '"deadlock_resolution"' in prompt
                assert "BINARY VERDICT" not in prompt
            elif effective == "binary":
                assert "BINARY VERDICT" in prompt and "DECIDING VOTE" not in prompt
            else:
                assert prompt.startswith("You are the Chairman of an LLM Council.")
                assert "BINARY VERDICT" not in prompt and "DECIDING VOTE" not in prompt
                assert all(candidate(m) in prompt for m in MODELS)
            prompts.append(prompt)
        calls.append((stage, model))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0125}})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return calls, ballots, prompts, mapping, effective, verdict, final


@pytest.mark.parametrize("requested,tied,outlier,include", [
    ("synthesis", False, True, True), ("synthesis", False, True, False),
    ("binary", False, True, True), ("binary", False, True, False),
    ("tie_breaker", False, True, True), ("tie_breaker", False, True, False),
    ("binary", True, True, True), ("binary", True, True, False),
    ("synthesis", False, False, True), ("binary", True, False, True),
], ids=["synthesis-dissent", "synthesis-no-dissent", "binary-dissent", "binary-no-dissent",
        "explicit-tie-dissent", "explicit-tie-no-dissent", "auto-tie-dissent", "auto-tie-no-dissent",
        "synthesis-no-outlier", "auto-tie-no-outlier"])
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_native_verdict_dissent(isolated_profile_config, monkeypatch, requested, tied, outlier, include, stream):
    from backend import main, storage

    assert main.run_full_council is council.run_full_council
    calls, ballots, prompts, mapping, effective, verdict, final = install_verdict_provider(
        monkeypatch, requested, tied, outlier)
    with TestClient(main.app) as client:
        settings = client.put("/api/settings", json={"profile": "high"})
        assert settings.status_code == 200 and settings.json()["models"] == MODELS
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        payload = decode_response(client.post(endpoint, json={"content": QUESTION,
                                  "verdict_type": requested, "include_dissent": include}), stream)
        assert Counter(calls) == Counter([("title", TITLE_MODEL)] + [("stage1", m) for m in MODELS]
                                        + [("stage2", m) for m in MODELS] + [("stage3", CHAIRMAN)])
        assert [stage for stage, _ in calls if stage != "title"] == ["stage1"] * 4 + ["stage2"] * 4 + ["stage3"]
        assert len(prompts) == 1
        assert {r["model"]: r["response"] for r in payload["stage1"]} == {m: candidate(m) for m in MODELS}
        assert len(payload["stage2"]) == 4
        for result in payload["stage2"]:
            ballot, raw = ballots[result["model"]]
            assert result["ranking"] == raw
            assert result["parsed_ranking"]["ranking"] == ballot["ranking"]
            assert result["parsed_ranking"]["scores"] == ballot["scores"]
        assert payload["stage3"] == {"model": CHAIRMAN, "response": final}
        metadata = payload["metadata"]
        assert metadata.get("status") not in ("failed", "partial")
        assert metadata["peer_review"]["state"] == "completed" and metadata["peer_review"]["effective_votes"] == 12
        assert {label: entry["model"] for label, entry in metadata["label_to_model"].items()} == mapping
        ranks = metadata["aggregate_rankings"]
        assert len(ranks) == 4 and all(r["vote_count"] == 3 for r in ranks)
        expected_scores = {model: 0.333 if tied else round((3 - LABELS.index(label)) / 3, 3)
                           for label, model in mapping.items()}
        assert {r["model"]: r["borda_score"] for r in ranks} == expected_scores
        config = metadata["config"]
        assert config["verdict_type"] == requested and config["effective_verdict_type"] == effective
        assert config["deadlock_detected"] is (requested == "binary" and tied)
        assert config["include_dissent"] is include
        expected_dissent = DISSENT if include and outlier else None
        if requested == "synthesis":
            assert "verdict" not in metadata
            assert metadata.get("dissent") == expected_dissent
        else:
            assert "dissent" not in metadata
            assert metadata["verdict"] == {
                "verdict_type": effective, "verdict": verdict["verdict"],
                "confidence": verdict["confidence"], "rationale": verdict["rationale"],
                "dissent": expected_dissent, "deadlocked": effective == "tie_breaker",
                "borda_spread": 0.0 if tied else 1.0,
            }
        if effective == "tie_breaker":
            expected_top = "\n".join(f"  - {r['model']}: Borda score {r['borda_score']}" for r in ranks[:3])
            assert "TOP CANDIDATES (within scoring threshold):\n" + expected_top in prompts[0]
        assert metadata["usage"]["total"]["total_tokens"] == 162
        disk = storage.get_conversation(cid)
        assert disk["title"] == TITLE
        assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **payload}]
        assert client.get(f"/api/conversations/{cid}").json() == disk
