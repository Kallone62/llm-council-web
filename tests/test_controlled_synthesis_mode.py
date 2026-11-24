"""Exercise mode switches through the real native Chairman HTTP boundary."""
import json
from collections import Counter

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from llm_council import council
from llm_council.unified_config import get_config, reload_config

from test_controlled_profiles import (
    KEY, PROFILES, TITLE, TITLE_MODEL, decode_response, deny_external_network,
    isolated_profile_config, review_text,
)

MODELS, CHAIRMAN, _, _ = PROFILES["high"]
QUESTION = "ENG15: Should we migrate the archive immediately or after verification?"
POSITIONS = dict(zip(MODELS, (
    "Migrate now to reduce downtime; verification can follow.",
    "Verify before migration to reduce the risk of losing records.",
    "Migrate in batches with checks between batches.",
    "Keep the old archive until a verified restore succeeds.",
)))
REVIEW = review_text(MODELS).replace(
    "All candidates support archive retention.",
    "The candidates disagree about migration timing and verification cost.",
)
HEADERS = (
    "## 1. Consensus Points", "## 2. Axes of Disagreement",
    "## 3. Position Summaries", "## 4. Crucial Assumptions",
    "## 5. Minority Reports", "## 6. Chairman's Assessment",
)
CONSENSUS_INSTRUCTION = "synthesize all of this information into a single, comprehensive, accurate answer"
DEBATE_INSTRUCTION = 'Do NOT flatten nuance into a single "best" answer.'
CONSENSUS_ANSWER = "Verify the archive, then migrate in batches to balance downtime and integrity."
DEBATE_ANSWER = "\n\n".join(f"{header}\n{body}" for header, body in zip(HEADERS, (
    "All positions address migration risk.", "Speed versus integrity.",
    "Migrate now versus verify first.", "Downtime tolerance differs.",
    "Keep the old archive until restore succeeds.", "Prefer verified batch migration.",
)))


def provider_for_mode(monkeypatch, mode):
    calls, chairman_prompts = [], []
    deny_external_network(monkeypatch, "Unexpected external network in synthesis-mode acceptance")

    async def handle(_transport, request):
        assert (request.method, str(request.url), request.headers.get("authorization")) == (
            "POST", "https://openrouter.ai/api/v1/chat/completions", f"Bearer {KEY}")
        body = json.loads(request.content)
        model = body["model"]
        prompt = body["messages"][-1]["content"]
        assert QUESTION in prompt
        if prompt.startswith("Generate a very short title"):
            stage, answer = "title", TITLE
            assert model == TITLE_MODEL
        elif prompt == QUESTION:
            stage, answer = "stage1", POSITIONS[model]
        elif "<responses_to_evaluate>" in prompt:
            stage, answer = "stage2", REVIEW
            assert model in MODELS and all(p in prompt for p in POSITIONS.values())
        else:
            stage = "stage3"
            chairman_prompts.append(prompt)
            assert model == CHAIRMAN
            assert prompt.startswith("You are the Chairman of an LLM Council.")
            assert all(f"Model: {m}\nResponse: {p}" in prompt for m, p in POSITIONS.items())
            assert all(f"Model: {m}\nRanking: {REVIEW}" in prompt for m in MODELS)
            assert "AGGREGATE RANKINGS" in prompt
            # The model fixture responds to the actual instructions, not the
            # selected settings value. Verify the whole mode-specific contract.
            if mode == "debate":
                assert "present a STRUCTURED ANALYSIS" in prompt
                assert "using EXACTLY these headers" in prompt
                assert all(prompt.count(h) == 1 for h in HEADERS)
                assert [prompt.index(h) for h in HEADERS] == sorted(prompt.index(h) for h in HEADERS)
                assert DEBATE_INSTRUCTION in prompt and "Include ALL 6 sections." in prompt
                assert CONSENSUS_INSTRUCTION not in prompt
                answer = DEBATE_ANSWER
            else:
                assert CONSENSUS_INSTRUCTION in prompt
                assert "represents the council's collective wisdom." in prompt
                assert DEBATE_INSTRUCTION not in prompt and not any(h in prompt for h in HEADERS)
                answer = CONSENSUS_ANSWER
        calls.append((stage, model))
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0125},
        })

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return calls, chairman_prompts


@pytest.mark.parametrize("modes", [("consensus", "debate"), ("debate", "consensus")],
                         ids=["consensus-to-debate", "debate-to-consensus"])
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_native_synthesis_mode_switch(isolated_profile_config, monkeypatch, modes, stream):
    from backend import main, storage

    assert main.run_full_council is council.run_full_council
    completed = {}
    with TestClient(main.app) as client:
        for mode in modes:
            calls, prompts = provider_for_mode(monkeypatch, mode)
            settings = client.put("/api/settings", json={"profile": "high", "synthesis_mode": mode})
            assert settings.status_code == 200 and settings.json()["synthesis_mode"] == mode
            saved = yaml.safe_load(isolated_profile_config.read_text(encoding="utf-8"))
            assert saved["council"]["synthesis_mode"] == mode
            reload_config()
            assert get_config().council.synthesis_mode == mode
            assert client.get("/api/settings").json()["synthesis_mode"] == mode
            assert not calls
            created = client.post("/api/conversations", json={})
            assert created.status_code == 200
            cid = created.json()["id"]
            endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
            payload = decode_response(client.post(endpoint, json={"content": QUESTION}), stream)
            assert len(prompts) == 1
            assert Counter(calls) == Counter([("title", TITLE_MODEL), ("stage3", CHAIRMAN)] + [
                (stage, model) for stage in ("stage1", "stage2") for model in MODELS])
            assert [stage for stage, _ in calls if stage != "title"] == ["stage1"] * 4 + ["stage2"] * 4 + ["stage3"]
            assert {r["model"]: r["response"] for r in payload["stage1"]} == POSITIONS
            assert len(payload["stage2"]) == 4 and {r["model"] for r in payload["stage2"]} == set(MODELS)
            assert all(r["ranking"] == REVIEW for r in payload["stage2"])
            expected = DEBATE_ANSWER if mode == "debate" else CONSENSUS_ANSWER
            assert payload["stage3"] == {"model": CHAIRMAN, "response": expected}
            metadata = payload["metadata"]
            assert metadata["config"]["synthesis_mode"] == mode
            assert metadata["config"]["effective_verdict_type"] == "synthesis" and "verdict" not in metadata
            assert metadata.get("status") not in ("partial", "failed")
            assert metadata["peer_review"]["state"] == "completed" and metadata["peer_review"]["effective_votes"] == 12
            assert all(r["vote_count"] == 3 for r in metadata["aggregate_rankings"])
            assert metadata["usage"]["total"]["total_tokens"] == 162
            disk = storage.get_conversation(cid)
            assert disk["title"] == TITLE
            assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **payload}]
            completed[cid] = disk
            # Changing synthesis mode must not rewrite a previously saved round.
            for record_id, record in completed.items():
                assert storage.get_conversation(record_id) == record
                assert client.get(f"/api/conversations/{record_id}").json() == record
