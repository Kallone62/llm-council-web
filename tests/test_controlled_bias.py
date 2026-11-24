"""ADR-015 bias auditing through real native provider HTTP, settings and storage."""
import asyncio
import json
import re
from collections import Counter

import httpx
import pytest
import yaml
from llm_council import council
from llm_council.unified_config import get_config, reload_config

from test_controlled_gateway_fallback import CHAIRMAN, KEYS, MODELS, PRIMARY, TITLE_MODEL, gateway_config

QUESTION = "ENG22: Compare archive policies."
TITLE = "Archive Retention Decision"
FINAL = "Keep a verified archive."
TEXTS = dict(zip(MODELS, ("Alpha", "Beta archive", "Gamma archive verified weekly", "Delta archive verified weekly with independent backup checks")))
# Rows are reviewer order; columns are candidate model order, not anonymous labels.
ROWS = {
    "off": [(1, 2, 4, 8)] * 4,
    "perfect": [(1, 2, 4, 8)] * 4,
    "negative": [(8, 4, 2, 1)] * 4,
    "flat": [(5, 5, 5, 5)] * 4,
    "calibration": [(1, 2, 3, 4), (4, 5, 6, 7), (4, 5, 6, 7), (7, 8, 9, 10)],
    "threshold": [(1, 2, 4, 8)] * 4,
    "partial": [("2", 4, None, True), (2, 4, "N/A", "NaN"), (), None],
    "no-scores": [(), (), (), None],
}
# Independent constants: sample variance of candidate means, sample SD of each
# reviewer's raw scores (native field is called variance), and approximate normal p.
EXPECTED = {
    "perfect": (1., 0., True, 9.583, True, [3.75]*4, [3.10]*4, [], [], "medium"),
    "negative": (-.843, .0264, True, 9.583, True, [3.75]*4, [3.10]*4, [], [], "medium"),
    "flat": (0., 1., False, 0., False, [5.]*4, [0.]*4, [], [], "low"),
    "calibration": (.959, 0., True, 1.667, True, [2.5,5.5,5.5,8.5], [1.29]*4, [MODELS[0]], [MODELS[3]], "high"),
    "threshold": (1., 0., False, 9.583, False, [3.75]*4, [3.10]*4, [], [], "low"),
    "partial": (0., 1., False, 2., True, [3.,3.], [1.41,1.41], [], [], "medium"),
    "no-scores": (0., 1., False, None, None, [], [], [], [], "low"),
}
FIELDS = ("length_score_correlation", "length_score_p_value", "length_bias_detected", "position_score_variance", "position_bias_detected", "reviewer_mean_scores", "reviewer_score_variance", "harsh_reviewers", "generous_reviewers", "overall_bias_risk")


def configure(path, monkeypatch, case):
    for key in ("BIAS_AUDIT_ENABLED", "BIAS_PERSISTENCE_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    settings = {"audit_enabled": case != "off", "persistence_enabled": False,
                "length_correlation_threshold": 1.0 if case == "threshold" else .3,
                "position_variance_threshold": 10.0 if case == "threshold" else .5}
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["evaluation"]["bias"] = settings
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    reload_config()
    return settings


def provider(monkeypatch, case):
    calls, sent, mappings = [], {}, []

    async def transport(_transport, request):
        assert request.method == "POST" and str(request.url) == PRIMARY
        assert request.headers["authorization"] == f"Bearer {KEYS[PRIMARY]}"
        body = json.loads(request.content)
        model, prompt = body["model"], body["messages"][-1]["content"]
        assert QUESTION in prompt
        if prompt == QUESTION:
            stage, answer = "stage1", TEXTS[model]
        elif "<responses_to_evaluate>" in prompt:
            stage = "stage2"
            labels = {value: "Response " + label for label, value in re.findall(
                r'<candidate_response id="([A-D])">\n(.*?)\n</candidate_response>', prompt, re.S)}
            assert set(labels) == set(TEXTS.values())
            mapped = {m: labels[v] for m, v in TEXTS.items()}
            mappings.append(mapped)
            row = ROWS[case][MODELS.index(model)]
            ranking = [mapped[m] for m in reversed(MODELS)]
            ballot = {"ranking": ranking, "scores": dict(zip((mapped[m] for m in MODELS), row or ())) }
            answer = "I cannot evaluate these responses." if row is None else json.dumps(ballot)
            sent[model] = (answer, ballot, row is None)
        elif prompt.startswith("Generate a very short title"):
            stage, answer = "title", TITLE
            assert model == TITLE_MODEL
        else:
            stage, answer = "stage3", FINAL
            assert model == CHAIRMAN and prompt.startswith("You are the Chairman of an LLM Council.")
            assert all(f"Model: {m}\nResponse: {v}" in prompt for m, v in TEXTS.items())
        calls.append((stage, model))
        return httpx.Response(200, json={"choices": [{"message": {"content": answer}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0}})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transport)
    return calls, sent, mappings


def verify(case, observed, stage1, stage2, stage3, metadata, title):
    calls, sent, mappings = observed
    expected_calls = [(s, m) for s in ("stage1", "stage2") for m in MODELS] + [("stage3", CHAIRMAN)]
    if title:
        expected_calls.append(("title", TITLE_MODEL))
    assert Counter(calls) == Counter(expected_calls)
    assert [s for s, _ in calls if s != "title"] == ["stage1"]*4 + ["stage2"]*4 + ["stage3"]
    assert {r["model"]: r["response"] for r in stage1} == TEXTS
    assert len(stage2) == len(mappings) == 4
    assert all(mapping == mappings[0] for mapping in mappings)
    assert metadata["label_to_model"] == {label: {"model": m, "display_index": ord(label[-1])-65} for m, label in mappings[0].items()}
    for review in stage2:
        raw, ballot, abstained = sent[review["model"]]
        assert review["ranking"] == raw
        parsed = review["parsed_ranking"]
        assert bool(parsed.get("abstained")) is abstained
        assert parsed["scores"] == ({} if abstained else ballot["scores"])
        assert parsed["ranking"] == ([] if abstained else ballot["ranking"])
    assert stage3 == {"model": CHAIRMAN, "response": FINAL}
    assert metadata["usage"]["total"]["total_tokens"] == 162
    if case == "off":
        assert "bias_audit" not in metadata
    else:
        values = list(EXPECTED[case])
        values[5] = dict(zip(MODELS, values[5]))
        values[6] = dict(zip(MODELS, values[6]))
        assert metadata["bias_audit"] == dict(zip(FIELDS, values))
    if title:
        partial = case in ("partial", "no-scores")
        review = metadata["peer_review"]
        assert review["state"] == ("partial" if partial else "completed")
        assert review["valid_reviews"] == (3 if partial else 4)
        assert review["abstained_reviews"] == (1 if partial else 0)
        assert review["effective_votes"] == (9 if partial else 12)


def decode_partial_response(response):
    """A partial Stage 2 must not be reported as completed in SSE (ENG-07)."""
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [event["type"] for event in events[-5:]] == [
        "stage1_complete", "stage2_complete", "stage3_complete", "complete", "title_complete"]
    assert events[-1]["data"] == {"title": TITLE}
    assert all(e["type"] == "engine_progress" for e in events[:-5])
    progress = [e["data"] for e in events[:-5]]
    assert [(e["stage"], e["state"]) for e in progress] == [
        ("stage1", "started"), ("stage1", "completed"),
        ("stage1_5", "started"), ("stage1_5", "completed"),
        ("stage2", "started"), ("stage2", "partial"),
        ("stage3", "started"), ("stage3", "completed")]
    metadata = events[-2]["metadata"]
    assert events[-4]["metadata"] == events[-3]["metadata"] == metadata
    assert metadata["ui_runtime_progress"] == progress
    return {"stage1": events[-5]["data"], "stage2": events[-4]["data"],
            "stage3": events[-3]["data"], "metadata": metadata}


@pytest.mark.parametrize("case", ROWS)
def test_native_bias(gateway_config, monkeypatch, case):
    configure(gateway_config, monkeypatch, case)
    observed = provider(monkeypatch, case)
    verify(case, observed, *asyncio.run(council.run_full_council(QUESTION, bypass_cache=True)), False)


@pytest.mark.parametrize("case", ROWS)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_bias_api(gateway_config, monkeypatch, tmp_path, case, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    from test_controlled_profiles import decode_response

    settings = configure(gateway_config, monkeypatch, case)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    observed = provider(monkeypatch, case)
    enabled = settings["audit_enabled"]
    with TestClient(main.app) as client:
        for value in (not enabled, enabled):
            response = client.put("/api/settings", json={"profile": "high", "bias_audit_enabled": value,
                "style_normalization": "off", "exclude_self_votes": True, "rubric_enabled": False, "safety_enabled": False})
            assert response.status_code == 200 and response.json()["bias_audit_enabled"] is value
        assert yaml.safe_load(gateway_config.read_text())["evaluation"]["bias"] == settings
        reload_config()
        assert get_config().evaluation.bias.audit_enabled is enabled
        assert client.get("/api/settings").json()["bias_audit_enabled"] is enabled
        assert not observed[0]
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        response = client.post(endpoint, json={"content": QUESTION})
        result = (decode_partial_response(response) if stream and case in ("partial", "no-scores")
                  else decode_response(response, stream))
        verify(case, observed, result["stage1"], result["stage2"], result["stage3"], result["metadata"], True)
        disk = storage.get_conversation(cid)
        assert disk["title"] == TITLE
        assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **result}]
        assert client.get(f"/api/conversations/{cid}").json() == disk
