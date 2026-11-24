"""Native usage accounting through real provider HTTP and application boundaries."""
import asyncio
import json
import re
from collections import Counter
from dataclasses import dataclass

import httpx
import pytest
import yaml
from llm_council import council
from llm_council.cost_summary import format_cost_summary
from llm_council.unified_config import get_config, reload_config

from test_controlled_gateway_fallback import (
    CHAIRMAN, KEYS, MODELS, PRIMARY, SECONDARY, TITLE_MODEL, gateway_config,
)

QUESTION = "ENG23: Compare archive policies."
TITLE = "Archive Retention Decision"
FINAL = "Keep a verified archive."
NORMALIZER = "google/gemini-3.5-flash-lite"
UNPRICED = "google/gemini-2.5-flash"
ALIAS = "requesty-fixture/chairman"
STAGES = ("stage1", "stage1_5", "stage2", "stage3")
CASES = ("off", "normalized", "mixed", "unknown", "missing", "fallback", "normalizer-error")
TEXTS = {model: f"Archive candidate {i}." for i, model in enumerate(MODELS)}
NORMALIZED = {model: f"Neutral archive candidate {i}." for i, model in enumerate(MODELS)}


@dataclass(frozen=True)
class Charge:
    stage: str
    model: str
    prompt: int
    completion: int
    cached: int
    written: int
    cost: float | None
    source: str | None = "provider"
    total: int | None = None


def expected_ledger(case):
    """Independent fixture oracle. Amounts are provider observations or fixed
    registry-price arithmetic, never calculated by a native usage helper.
    """
    rows = []
    for i, model in enumerate(MODELS):
        rows.append(Charge("stage1", model, 100 + i*10, 20+i, i, i*2, (0., .02, .03, .04)[i]))
    if case != "off":
        normalizer = UNPRICED if case == "unknown" else NORMALIZER
        for i in range(4):
            if case == "normalizer-error" and i == 1:
                continue
            if case == "missing":
                # null / malformed / incomplete token telemetry, then a real
                # provider-reported zero with the same missing token telemetry.
                rows.append(Charge("stage1_5", normalizer, 50 if i == 2 else 0, 0, 0, 0,
                                   0. if i == 3 else None, "provider" if i == 3 else None, total=0))
            elif case == "unknown":
                rows.append(Charge("stage1_5", normalizer, 50+i, 10+i, 0, 0, None, None))
            else:
                rows.append(Charge("stage1_5", normalizer, 50+i, 10+i, i, i*2, (.001, .002, .003, .004)[i]))
    for i, model in enumerate(MODELS):
        if case == "mixed" and i == 0:
            # GPT-5.6 Sol registry: .002/.010 USD per 1k input/output tokens.
            # 200*.002/1000 + 30*.010/1000 = .0007.
            rows.append(Charge("stage2", model, 200, 30, 0, 0, .0007, "registry_estimate"))
        else:
            rows.append(Charge("stage2", model, 200+i*10, 30+i, i, i*2, (.05, .06, .07, .08)[i]))
    # The alias has no registry entry: the fallback estimate MUST use the
    # canonical model id, not the wire id; 300*.002/1000+40*.010/1000=.001.
    rows.append(Charge("stage3", CHAIRMAN, 300, 40, 0, 0,
                       .001 if case == "fallback" else .09,
                       "registry_estimate" if case == "fallback" else "provider"))
    return rows


def configure(path, case):
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["council"]["style_normalization"] = case != "off"
    cfg["council"]["normalizer_model"] = UNPRICED if case == "unknown" else NORMALIZER
    cfg["gateways"]["fallback"]["enabled"] = case == "fallback"
    cfg["gateways"]["model_name_map"] = {"requesty": {CHAIRMAN: ALIAS}}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    reload_config()


def install_provider(monkeypatch, case):
    calls = []
    sent = {}
    review_text = dict(TEXTS if case == "off" else NORMALIZED)
    if case == "normalizer-error":
        review_text[MODELS[1]] = TEXTS[MODELS[1]]

    async def transport(_transport, request):
        url = str(request.url)
        assert request.method == "POST" and url in KEYS
        assert request.headers["authorization"] == f"Bearer {KEYS[url]}"
        body = json.loads(request.content)
        model, prompt = body["model"], body["messages"][-1]["content"]
        if prompt == QUESTION:
            stage, i, answer = "stage1", MODELS.index(model), TEXTS[model]
            usage = {"prompt_tokens": 100+i*10, "completion_tokens": 20+i,
                     "total_tokens": 120+i*11, "cost": (0., .02, .03, .04)[i]}
        elif prompt.startswith("Rewrite the following text"):
            stage = "stage1_5"
            assert model == (UNPRICED if case == "unknown" else NORMALIZER)
            candidate = next(m for m, text in TEXTS.items() if text in prompt)
            i, answer = MODELS.index(candidate), NORMALIZED[candidate]
            if case == "normalizer-error" and i == 1:
                calls.append((stage, model, url, 401))
                return httpx.Response(401, json={"error": {"message": "fixture auth failure"}})
            if case == "missing":
                usage = (None, {"prompt_tokens": "bad", "completion_tokens": False},
                         {"prompt_tokens": 50}, {"cost": 0.})[i]
            else:
                usage = {"prompt_tokens": 50+i, "completion_tokens": 10+i, "total_tokens": 60+i*2}
                if case != "unknown":
                    usage["cost"] = (.001, .002, .003, .004)[i]
        elif "<responses_to_evaluate>" in prompt:
            stage, i = "stage2", MODELS.index(model)
            labels = {value: "Response " + label for label, value in re.findall(
                r'<candidate_response id="([A-D])">\n(.*?)\n</candidate_response>', prompt, re.S)}
            assert set(labels) == set(review_text.values())
            ranking = [labels[review_text[m]] for m in MODELS]
            answer = json.dumps({"ranking": ranking, "scores": dict(zip(ranking, (9, 7, 5, 3)))})
            sent[model] = answer
            usage = {"prompt_tokens": 200+i*10, "completion_tokens": 30+i, "total_tokens": 230+i*11}
            if not (case == "mixed" and i == 0):
                usage["cost"] = (.05, .06, .07, .08)[i]
        elif prompt.startswith("Generate a very short title"):
            assert model == TITLE_MODEL and url == PRIMARY
            calls.append(("title", model, url, 200))
            return httpx.Response(200, json={"choices": [{"message": {"content": TITLE}}],
                "usage": {"prompt_tokens": 999, "completion_tokens": 1, "total_tokens": 1000, "cost": .99}})
        else:
            stage, i, answer = "stage3", 0, FINAL
            assert prompt.startswith("You are the Chairman of an LLM Council.")
            assert all(f"Model: {m}\nResponse: {text}" in prompt for m, text in TEXTS.items())
            assert all(f"Model: {m}\nRanking: {raw}" in prompt for m, raw in sent.items())
            if case == "fallback" and url == PRIMARY:
                assert model == CHAIRMAN
                calls.append((stage, model, url, 503))
                return httpx.Response(503, json={"error": {"message": "fixture unavailable"}})
            assert (url, model) == ((SECONDARY, ALIAS) if case == "fallback" else (PRIMARY, CHAIRMAN))
            usage = {"prompt_tokens": 300, "completion_tokens": 40, "total_tokens": 340}
            if case != "fallback":
                usage["cost"] = .09
        if usage is not None and "cost" in usage and stage != "stage3" and not (case == "missing" and stage == "stage1_5"):
            usage["prompt_tokens_details"] = {"cached_tokens": i, "cache_write_tokens": i*2}
        if stage != "stage3":
            assert url == PRIMARY
        calls.append((stage, model, url, 200))
        return httpx.Response(200, json={"choices": [{"message": {"content": answer}}], "usage": usage})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transport)
    return calls, sent


def check_bucket(bucket, rows):
    assert bucket.get("requests", 0) == len(rows)
    for key, value in {
        "prompt_tokens": sum(r.prompt for r in rows), "completion_tokens": sum(r.completion for r in rows),
        "total_tokens": sum(r.prompt+r.completion if r.total is None else r.total for r in rows),
        "cached_tokens": sum(r.cached for r in rows), "cache_write_tokens": sum(r.written for r in rows),
        "cost_usd": sum(r.cost or 0 for r in rows),
        "cost_observed_usd": sum(r.cost or 0 for r in rows if r.source == "provider"),
        "cost_estimated_usd": sum(r.cost or 0 for r in rows if r.source == "registry_estimate"),
    }.items():
        assert (bucket[key] if rows else bucket.get(key, 0)) == pytest.approx(value, abs=1e-10), key
    sources = sorted({r.source for r in rows if r.source})
    assert bucket.get("cost_sources", []) == sources
    assert bucket.get("cost_source") == ("mixed" if len(sources) > 1 else sources[0] if sources else None)
    assert bool(bucket.get("cost_known")) == any(r.cost is not None for r in rows)
    assert bool(bucket.get("cost_incomplete")) == any(r.cost is None for r in rows)
    assert not bucket.get("cost_unattributed")


def verify(case, observed, stage1, stage2, stage3, metadata, title):
    calls, sent = observed
    rows = expected_ledger(case)
    expected_calls = [(r.stage, r.model, PRIMARY, 200) for r in rows if r.stage != "stage3"]
    if case == "fallback":
        expected_calls += [("stage3", CHAIRMAN, PRIMARY, 503), ("stage3", ALIAS, SECONDARY, 200)]
    else:
        expected_calls.append(("stage3", CHAIRMAN, PRIMARY, 200))
    if case == "normalizer-error":
        expected_calls.append(("stage1_5", NORMALIZER, PRIMARY, 401))
    if title:
        expected_calls.append(("title", TITLE_MODEL, PRIMARY, 200))
    assert Counter(calls) == Counter(expected_calls)
    stages = [STAGES.index(s) for s, _, _, _ in calls if s != "title"]
    assert stages == sorted(stages)
    assert {r["model"]: r["response"] for r in stage1} == TEXTS
    assert {r["model"]: r["ranking"] for r in stage2} == sent
    assert stage3 == {"model": CHAIRMAN, "response": FINAL}
    usage = metadata["usage"]
    assert set(usage["by_stage"]) == set(STAGES)
    for stage in STAGES:
        subset = [r for r in rows if r.stage == stage]
        bucket = usage["by_stage"][stage]
        check_bucket(bucket, subset)
        models = {r.model for r in subset}
        assert set(bucket.get("by_model", {})) == models
        for model in models:
            check_bucket(bucket["by_model"][model], [r for r in subset if r.model == model])
    models = {r.model for r in rows}
    assert set(usage["by_model"]) == models
    for model in models:
        check_bucket(usage["by_model"][model], [r for r in rows if r.model == model])
    check_bucket(usage["total"], rows)
    # Title traffic is deliberately distinct: native Council usage is not an
    # invoice for the whole application. No title or wire-alias bucket.
    if title:
        assert metadata["peer_review"]["state"] == "completed"
        assert metadata["peer_review"]["effective_votes"] == 12
    summary = format_cost_summary(usage, include_details=True)
    if case in ("missing", "unknown"):
        assert "cost incomplete" in summary.splitlines()[0]
        stage_line = next(l for l in summary.splitlines() if l.startswith("- stage1_5:"))
        if case == "missing":
            assert "$0.0000" in stage_line and "cost incomplete" in stage_line
        else:
            assert "$" not in stage_line and "cost unknown" in stage_line
        model_line = next(l for l in summary.splitlines() if l.startswith(f"- {NORMALIZER if case == 'missing' else UNPRICED}:"))
        if case == "missing":
            assert "$0.0000" in model_line and "cost incomplete" in model_line
        else:
            assert "$" not in model_line and "cost unknown" in model_line
    else:
        assert "cost incomplete" not in summary and "cost unknown" not in summary
    if case in ("mixed", "fallback"):
        assert "estimated" in summary.splitlines()[0]


@pytest.mark.parametrize("case", CASES)
def test_native_usage(gateway_config, monkeypatch, case):
    configure(gateway_config, case)
    observed = install_provider(monkeypatch, case)
    verify(case, observed, *asyncio.run(council.run_full_council(QUESTION, bypass_cache=True)), False)


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_usage_api(gateway_config, monkeypatch, tmp_path, case, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    from test_controlled_profiles import decode_response

    configure(gateway_config, case)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    observed = install_provider(monkeypatch, case)
    mode = "off" if case == "off" else "on"
    with TestClient(main.app) as client:
        settings = client.put("/api/settings", json={"profile": "high", "style_normalization": mode,
            "exclude_self_votes": True, "rubric_enabled": False, "safety_enabled": False, "bias_audit_enabled": False})
        assert settings.status_code == 200 and settings.json()["style_normalization"] == mode
        cfg = yaml.safe_load(gateway_config.read_text())
        assert cfg["council"]["style_normalization"] is (case != "off")
        assert cfg["council"]["normalizer_model"] == (UNPRICED if case == "unknown" else NORMALIZER)
        assert cfg["gateways"]["fallback"]["enabled"] is (case == "fallback")
        reload_config()
        assert get_config().council.style_normalization is (case != "off")
        assert get_config().gateways.fallback.enabled is (case == "fallback")
        assert not observed[0]
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        path = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = decode_response(client.post(path, json={"content": QUESTION}), stream)
        verify(case, observed, result["stage1"], result["stage2"], result["stage3"], result["metadata"], True)
        disk = storage.get_conversation(cid)
        assert disk["title"] == TITLE
        assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **result}]
        assert client.get(f"/api/conversations/{cid}").json() == disk
