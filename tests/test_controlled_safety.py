"""ADR-016 safety flags and score caps at the real native HTTP boundary."""
import asyncio
import json
import re
from collections import Counter

import httpx
import pytest
import yaml
from llm_council import council
from llm_council.unified_config import get_config, reload_config

from test_controlled_gateway_fallback import (
    CHAIRMAN, KEYS, MODELS, PRIMARY, TITLE_MODEL, gateway_config,
)

QUESTION = "ENG21: Assess archive handling."
TITLE = "Archive Retention Decision"
FINAL = "Retain a verified archive."
# Deliberately inert synthetic strings: no credentials or actionable instructions.
TEXTS = (
    "Archive checksums and backups.",
    "Fixture password: SYNTHETIC_ONLY",
    "Prevent access without permission.",
    "Fixture access without permission; password: SYNTHETIC_OTHER",
)
SAFE = {"passed": True, "reason": None, "flagged_patterns": []}
PII = "Contains exposed personally identifiable information (PII)"
ACCESS = "Contains malware or unauthorized access instructions"
FLAGS = (SAFE, {"passed": False, "reason": PII, "flagged_patterns": ["pii_exposure"]}, SAFE,
         {"passed": False, "reason": ACCESS + "; " + PII, "flagged_patterns": ["malware_hacking", "pii_exposure"]})
CASES = {
    "off": (False, 0.0, (.9, .633, .2, .8)),
    "zero": (True, 0.0, (.9, 0.0, .2, 0.0)),
    "custom": (True, .25, (.9, .2, .2, .25)),
    "rubric": (True, .25, (.9, .2, .2, .25)),
    "safe": (True, 0.0, (.9, .633, .2, .8)),
    "missing-score": (True, .25, (.9, None, .2, .25)),
}


def texts(case):
    return dict(zip(MODELS, TEXTS if case != "safe" else tuple(f"Archive candidate {i}." for i in range(4))))


def configure(path, monkeypatch, case):
    monkeypatch.delenv("SAFETY_GATE_ENABLED", raising=False)
    enabled, cap, _ = CASES[case]
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["evaluation"]["safety"] = {"enabled": enabled, "score_cap": cap}
    cfg["evaluation"]["rubric"] = {"enabled": case == "rubric", "accuracy_ceiling_enabled": False}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    reload_config()


def provider(monkeypatch, case):
    calls, sent, prompts = [], {}, []
    responses = texts(case)

    async def transport(_transport, request):
        assert request.method == "POST" and str(request.url) == PRIMARY
        assert request.headers["authorization"] == f"Bearer {KEYS[PRIMARY]}"
        body = json.loads(request.content)
        model, prompt = body["model"], body["messages"][-1]["content"]
        assert QUESTION in prompt
        if prompt == QUESTION:
            stage, answer = "stage1", responses[model]
        elif "<responses_to_evaluate>" in prompt:
            stage = "stage2"
            labels = {value: "Response " + label for label, value in re.findall(
                r'<candidate_response id="([A-D])">\n(.*?)\n</candidate_response>', prompt, re.S)}
            assert set(labels) == set(responses.values())
            mapped = {m: labels[v] for m, v in responses.items()}
            scores = dict(zip((mapped[m] for m in MODELS), (9, 1 if model == MODELS[0] else 9, 2, 8)))
            if case == "missing-score":
                del scores[mapped[MODELS[1]]]
            ballot = {"ranking": [mapped[m] for m in (MODELS[1], MODELS[3], MODELS[0], MODELS[2])], "scores": scores}
            if case == "rubric":
                assert "EVALUATION RUBRIC" in prompt
                evaluations = {label: dict.fromkeys(("accuracy", "relevance", "completeness", "conciseness", "clarity"), score)
                               for label, score in scores.items()}
                answer = json.dumps({"ranking": ballot["ranking"], "evaluations": evaluations})
            else:
                assert "EVALUATION RUBRIC" not in prompt
                answer = json.dumps(ballot)
            sent[model] = (answer, ballot)
        elif prompt.startswith("Generate a very short title"):
            stage, answer = "title", TITLE
            assert model == TITLE_MODEL
        else:
            stage, answer = "stage3", FINAL
            assert model == CHAIRMAN and prompt.startswith("You are the Chairman of an LLM Council.")
            assert all(f"Model: {m}\nResponse: {v}" in prompt for m, v in responses.items())
            assert all(f"Model: {m}\nRanking: {raw}" in prompt for m, (raw, _) in sent.items())
            prompts.append(prompt)
        calls.append((stage, model))
        return httpx.Response(200, json={"choices": [{"message": {"content": answer}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0}})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transport)
    return calls, sent, prompts


def verify(case, observed, stage1, stage2, stage3, metadata, title):
    enabled, cap, scores = CASES[case]
    calls, sent, prompts = observed
    expected = [(s, m) for s in ("stage1", "stage2") for m in MODELS] + [("stage3", CHAIRMAN)]
    if title:
        expected.append(("title", TITLE_MODEL))
    assert Counter(calls) == Counter(expected)
    assert [s for s, _ in calls if s != "title"] == ["stage1"]*4 + ["stage2"]*4 + ["stage3"]
    assert {r["model"]: r["response"] for r in stage1} == texts(case)
    if enabled:
        flags = dict(zip(MODELS, FLAGS if case != "safe" else (SAFE,)*4))
        assert {r["model"]: r["safety_check"] for r in stage1} == flags
        gate = metadata["safety_gate"]
        assert gate == {"enabled": True, "results": flags, "score_cap": cap,
                        "failed_models": [r["model"] for r in stage1 if not flags[r["model"]]["passed"]]}
    else:
        assert "safety_gate" not in metadata and all("safety_check" not in r for r in stage1)
    assert len(stage2) == 4
    for review in stage2:
        raw, ballot = sent[review["model"]]
        assert review["ranking"] == raw
        assert review["parsed_ranking"]["scores"] == ballot["scores"]
        assert review["parsed_ranking"]["ranking"] == ballot["ranking"]
        if case == "rubric":
            assert review["parsed_ranking"]["rubric_scoring"] is True
            assert review["parsed_ranking"]["evaluations"] == json.loads(raw)["evaluations"]
    ranks = metadata["aggregate_rankings"]
    assert [r["model"] for r in ranks] == [MODELS[i] for i in (1,3,0,2)]
    assert [r["borda_score"] for r in ranks] == [1.0,.667,.333,0.0]
    assert [r["average_position"] for r in ranks] == [1.0,2.0,3.0,4.0]
    assert all(r["vote_count"] == 3 for r in ranks)
    assert {r["model"]: r["average_score"] for r in ranks} == dict(zip(MODELS, scores))
    assert len(prompts) == 1
    for rank in ranks:
        assert f'#{rank["rank"]}. {rank["model"]} (avg score: {rank["average_score"]}, votes: 3)' in prompts[0]
    assert stage3 == {"model": CHAIRMAN, "response": FINAL}
    assert metadata["usage"]["total"]["total_tokens"] == 162
    if title:
        assert metadata["peer_review"]["state"] == "completed"
        assert metadata["peer_review"]["effective_votes"] == 12


@pytest.mark.parametrize("case", CASES)
def test_native_safety(gateway_config, monkeypatch, case):
    configure(gateway_config, monkeypatch, case)
    observed = provider(monkeypatch, case)
    verify(case, observed, *asyncio.run(council.run_full_council(QUESTION, bypass_cache=True)), False)


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_safety_api(gateway_config, monkeypatch, tmp_path, case, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    from test_controlled_profiles import decode_response

    configure(gateway_config, monkeypatch, case)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    observed = provider(monkeypatch, case)
    enabled, cap, _ = CASES[case]
    with TestClient(main.app) as client:
        for value in (not enabled, enabled):
            response = client.put("/api/settings", json={"profile": "high", "safety_enabled": value,
                "style_normalization": "off", "exclude_self_votes": True, "rubric_enabled": case == "rubric"})
            assert response.status_code == 200 and response.json()["safety_enabled"] is value
        assert yaml.safe_load(gateway_config.read_text())["evaluation"]["safety"] == {"enabled": enabled, "score_cap": cap}
        reload_config()
        assert get_config().evaluation.safety.enabled is enabled
        assert get_config().evaluation.safety.score_cap == cap
        assert client.get("/api/settings").json()["safety_enabled"] is enabled
        assert not observed[0]
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = decode_response(client.post(endpoint, json={"content": QUESTION}), stream)
        verify(case, observed, result["stage1"], result["stage2"], result["stage3"], result["metadata"], True)
        disk = storage.get_conversation(cid)
        assert disk["title"] == TITLE
        assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **result}]
        assert client.get(f"/api/conversations/{cid}").json() == disk
