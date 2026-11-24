"""ENG-01: exercise native deliberation through the provider HTTP boundary only."""
import asyncio
from collections import Counter
import json
import os
import socket

import httpx
import pytest
import yaml

from backend import engine_runtime
from backend.free_profile import FREE_CHAIRMAN, FREE_MODELS
from llm_council import council
from llm_council.unified_config import get_config, reload_config

QUESTION = "ENG-01: Which approach preserves the original data?"
ANSWERS = {model: f"Candidate {i}: preserve original data before updating." for i, model in enumerate(FREE_MODELS, 1)}
LABELS = ["Response A", "Response B", "Response C"]
REVIEW_TEXT = "All candidates address preservation.\n```json\n" + json.dumps({
    "ranking": LABELS, "scores": dict(zip(LABELS, [9, 8, 7])),
}) + "\n```"
FINAL_TEXT = "Preserve the original data before applying an atomic update."
USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0}


@pytest.fixture
def provider_http(tmp_path, monkeypatch, request):
    """Fresh real engine config; intercept only actual outgoing HTTP requests."""
    # No local API secrets, ambient config overrides, proxies or telemetry.
    for name in tuple(os.environ):
        if name.startswith(("LLM_COUNCIL_", "OTEL_", "POSTHOG_")) or name.lower().endswith("_proxy"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "eng-01-test-only-not-a-real-key")
    monkeypatch.setenv("LLM_COUNCIL_PERFORMANCE_STORE", str(tmp_path / "performance.jsonl"))
    monkeypatch.setenv("LLM_COUNCIL_PERFORMANCE_TRACKING", "false")
    monkeypatch.setenv("LLM_COUNCIL_EARLY_CONSENSUS", "false")
    scenario = getattr(request, "param", None)
    infra = scenario.split("-") if isinstance(scenario, str) and scenario.startswith("infra-") else None
    config_path = tmp_path / "eng01.yaml"
    config_path.write_text(yaml.safe_dump({
        "tiers": {"default": "high"},
        "council": {"models": list(FREE_MODELS), "chairman": FREE_CHAIRMAN,
                    "synthesis_mode": "consensus", "exclude_self_votes": True,
                    "style_normalization": False,
                    **({"max_reviewers": 2} if scenario == "review-cap" else {})},
        "gateways": {"default": "openrouter", "fallback": {"enabled": False}},
        "evaluation": {"rubric": {"enabled": False}, "safety": {"enabled": False},
                       "bias": {"audit_enabled": False, "persistence_enabled": False}},
        "cache": {"enabled": False, "ttl_seconds": 0},
        **({"timeouts": {"multiplier": 2.0}} if infra else {}),
    }), encoding="utf-8")
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(config_path))
    reload_config()
    calls = []
    quota_stage = scenario.removeprefix("quota-") if isinstance(scenario, str) and scenario.startswith("quota-") else None
    rate_stage1 = scenario in ("rate-stage1-member", "rate-stage1-chairman")
    rate_stage2 = scenario in ("rate-stage2-member", "rate-stage2-chairman")
    rate_stage3 = scenario == "rate-stage3-chairman"
    limited_model = FREE_CHAIRMAN if scenario in ("rate-stage1-chairman", "rate-stage2-chairman", "rate-stage3-chairman") else FREE_MODELS[-1]
    failed_model = limited_model if rate_stage1 else None if rate_stage2 or rate_stage3 or quota_stage or scenario == "review-cap" else scenario
    if infra:
        failed_model = FREE_MODELS[-1] if infra[1] == "stage1" else None
    failed_models = set(FREE_MODELS) if failed_model == "all" or quota_stage == "stage1" else {failed_model}
    available = {model: answer for model, answer in ANSWERS.items() if model not in failed_models}
    labels = LABELS[:len(available)]
    review_text = REVIEW_TEXT if failed_model is None else "All candidates address preservation.\n```json\n" + json.dumps({
        "ranking": labels, "scores": dict(zip(labels, [9, 8, 7])),
    }) + "\n```"

    def reply(request):
        assert request.method == "POST"
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        if request.headers.get("authorization") != "Bearer eng-01-test-only-not-a-real-key":
            pytest.fail("Unexpected provider authentication header (redacted)")
        body = json.loads(request.content)
        model = body["model"]
        assert model in ANSWERS
        prompt = body["messages"][-1]["content"]
        chairman_context_matches = None
        if prompt == QUESTION:
            stage, content = "stage1", ANSWERS[model]
        elif "<responses_to_evaluate>" in prompt:
            stage, content = "stage2", review_text
            assert QUESTION in prompt and all(answer in prompt for answer in available.values())
            assert all(ANSWERS[model] not in prompt for model in failed_models if model is not None)
        elif prompt.startswith("You are the Chairman of an LLM Council."):
            stage, content = "stage3", FINAL_TEXT
            if not (rate_stage1 or rate_stage2 or rate_stage3 or quota_stage or infra):
                assert model == FREE_CHAIRMAN
            assert QUESTION in prompt and all(answer in prompt for answer in available.values())
            assert all(ANSWERS[model] not in prompt for model in failed_models if model is not None)
            chairman_context_matches = "AGGREGATE RANKINGS" in prompt and review_text in prompt
        else:
            pytest.fail("Unexpected provider prompt (no credentials or prompt text logged)")
        # Record no credentials or raw prompts. Native HTTP client parses the response.
        if infra:
            expected_timeout = 120.0 if stage == "stage1" else 180.0
            assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), expected_timeout)
            onset, failure = infra[1:]
            target = FREE_CHAIRMAN if onset == "stage3" else FREE_MODELS[-1]
            failing = ((onset == "stage1" and stage in ("stage1", "stage2") and model == target)
                       or (stage == onset and (model == target or failure == "all503")))
            if failing:
                code = 503 if failure in ("503", "all503") else failure
                calls.append((stage, model, request.url.host, code, chairman_context_matches))
                if failure == "timeout":
                    raise httpx.ReadTimeout("Controlled read timeout", request=request)
                if failure == "connect":
                    raise httpx.ConnectError("Controlled connection failure", request=request)
                return httpx.Response(503, json={"error": {"code": 503, "message": "Service unavailable"}})
        if quota_stage and int(stage[-1]) >= int(quota_stage[-1]):
            calls.append((stage, model, request.url.host, 429, chairman_context_matches))
            return httpx.Response(429, headers={"Retry-After": "3600"}, json={"error": {"code": 429, "message": "Daily free request quota exhausted for this account"}})
        if model == limited_model and (rate_stage1 or (rate_stage2 and stage in ("stage2", "stage3")) or (rate_stage3 and stage == "stage3")):
            calls.append((stage, model, request.url.host, 429, chairman_context_matches))
            return httpx.Response(429, headers={"Retry-After": "120"}, json={"error": {"code": 429, "message": "Model rate limited"}})
        if model in failed_models:
            calls.append((stage, model, request.url.host, 404, chairman_context_matches))
            return httpx.Response(404, json={"error": {"code": 404, "message": "No endpoints found for this model"}})
        calls.append((stage, model, request.url.host, 200, chairman_context_matches))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}], "usage": USAGE})

    transport = httpx.MockTransport(reply)

    async def handle(_self, request):
        return await transport.handle_async_request(request)

    def deny_network(*args, **kwargs):
        pytest.fail("Unexpected real network connection during controlled integration")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    original_connect = socket.socket.connect

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            deny_network()
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    assert get_config().council.models == list(FREE_MODELS)
    assert get_config().council.chairman == FREE_CHAIRMAN
    assert not get_config().cache.enabled
    yield calls
    # Shared conftest restores the original configuration after this fixture.


def assert_round(stage1, stage2, stage3, metadata, calls):
    assert Counter((stage, model) for stage, model, _, _, _ in calls) == Counter(
        [(stage, model) for stage in ("stage1", "stage2") for model in FREE_MODELS]
        + [("stage3", FREE_CHAIRMAN)]
    )
    assert [stage for stage, *_ in calls] == ["stage1"] * 3 + ["stage2"] * 3 + ["stage3"]
    assert all(host == "openrouter.ai" and status == 200 for _, _, host, status, _ in calls)
    assert len(stage1) == 3 and {r["model"]: r["response"] for r in stage1} == ANSWERS
    assert len(stage2) == 3 and {r["model"] for r in stage2} == set(FREE_MODELS)
    for review in stage2:
        assert review["parsed_ranking"]["ranking"] == LABELS
        assert review["ranking"] == REVIEW_TEXT
        assert not review["parsed_ranking"].get("abstained")
    mapping = metadata["label_to_model"]
    assert set(mapping) == set(LABELS)
    assert {item["model"] for item in mapping.values()} == set(FREE_MODELS)
    assert [mapping[label]["display_index"] for label in LABELS] == [0, 1, 2]
    aggregate = metadata["aggregate_rankings"]
    assert len(aggregate) == 3 and {r["model"] for r in aggregate} == set(FREE_MODELS)
    assert all(r["vote_count"] == 2 for r in aggregate)
    assert aggregate[0]["model"] == mapping["Response A"]["model"]
    assert stage3["model"] == FREE_CHAIRMAN and stage3["response"] == FINAL_TEXT
    assert not stage3.get("error_status") and not metadata.get("degraded_mode")
    assert metadata.get("status") not in ("failed", "partial")
    assert not metadata.get("cache_hit")
    assert metadata["config"]["responses_received"] == 3
    assert metadata["config"]["exclude_self_votes"] is True
    usage = metadata["usage"]
    for stage, count in (("stage1", 3), ("stage1_5", 0), ("stage2", 3), ("stage3", 1)):
        assert usage["by_stage"][stage]["total_tokens"] == count * USAGE["total_tokens"]
    assert usage["total"]["total_tokens"] == 7 * USAGE["total_tokens"]
    assert usage["total"]["cost_usd"] == 0 and usage["total"]["cost_known"] is True
    assert usage["total"]["requests"] == 7
    assert [context for stage, *_, context in calls if stage == "stage3"] == [True]


def assert_progress(events):
    assert [(e["stage"], e["state"]) for e in events] == [
        (stage, state) for stage in ("stage1", "stage1_5", "stage2", "stage3")
        for state in ("started", "completed")
    ]
    assert events[1]["responses"] == 3
    assert events[5]["valid_reviews"] == 3 and events[5]["reviews"] == 3


def test_eng01_native_round(provider_http):
    """Real pinned-native orchestration and actual application's stage wrappers."""
    engine_runtime.install_stage_hooks()
    events = []

    async def run():
        async def progress(event):
            events.append(event)
        token = engine_runtime.watch_progress(progress)
        try:
            return await council.run_full_council(QUESTION)
        finally:
            engine_runtime.unwatch_progress(token)

    assert_round(*asyncio.run(run()), provider_http)
    assert_progress(events)


@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_eng01_actual_api_and_disk(provider_http, tmp_path, monkeypatch, stream):
    """No replacement of FastAPI, native Council, stages or storage."""
    from fastapi.testclient import TestClient
    from backend import main, storage

    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = client.post(endpoint, json={"content": QUESTION})
        assert result.status_code == 200, result.text
        if stream:
            assert result.headers["content-type"].startswith("text/event-stream")
            events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
            assert [e["type"] for e in events] == ["engine_progress"] * 8 + [
                "stage1_complete", "stage2_complete", "stage3_complete", "complete"]
            progress = [e["data"] for e in events[:8]]
            assert_progress(progress)
            stage1, stage2, stage3 = [e["data"] for e in events[8:11]]
            metadata = events[11]["metadata"]
            assert events[9]["metadata"] == events[10]["metadata"] == metadata
            assert metadata["ui_runtime_progress"] == progress
        else:
            payload = result.json()
            stage1, stage2, stage3, metadata = [payload[key] for key in ("stage1", "stage2", "stage3", "metadata")]
        assert_round(stage1, stage2, stage3, metadata, provider_http)
        assert metadata["peer_review"]["state"] == "completed"
        assert metadata["peer_review"]["effective_votes"] == 6
        assert metadata["peer_review"]["ranked_candidates"] == 3
        persisted = json.loads((tmp_path / "conversations" / f"{cid}.json").read_text(encoding="utf-8"))
        assert persisted["messages"] == [
            {"role": "user", "content": QUESTION},
            {"role": "assistant", "stage1": stage1, "stage2": stage2, "stage3": stage3, "metadata": metadata},
        ]
        reloaded = client.get(f"/api/conversations/{cid}")
        assert reloaded.status_code == 200 and reloaded.json()["messages"] == persisted["messages"]


@pytest.mark.parametrize("provider_http", [FREE_MODELS[-1]], indirect=True)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_eng02_single_unavailable_model(provider_http, tmp_path, monkeypatch, stream):
    """A real 404 at the HTTP boundary must leave an explicitly partial round."""
    from fastapi.testclient import TestClient
    from backend import main, storage

    failed_model = FREE_MODELS[-1]
    available = {model: answer for model, answer in ANSWERS.items() if model != failed_model}
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = client.post(endpoint, json={"content": QUESTION})
        assert result.status_code == 200, result.text
        if stream:
            assert result.headers["content-type"].startswith("text/event-stream")
            events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
            assert [e["type"] for e in events] == ["engine_progress"] * 8 + [
                "stage1_complete", "stage2_complete", "stage3_complete", "complete"]
            progress = [e["data"] for e in events[:8]]
            assert [(e["stage"], e["state"]) for e in progress] == [
                (stage, state) for stage in ("stage1", "stage1_5", "stage2", "stage3")
                for state in ("started", "completed")]
            assert progress[1]["responses"] == 2
            assert progress[5]["valid_reviews"] == 2 and progress[5]["reviews"] == 2
            stage1, stage2, stage3 = [e["data"] for e in events[8:11]]
            metadata = events[11]["metadata"]
            assert events[9]["metadata"] == events[10]["metadata"] == metadata
            assert metadata["ui_runtime_progress"] == progress
        else:
            payload = result.json()
            stage1, stage2, stage3, metadata = [payload[key] for key in ("stage1", "stage2", "stage3", "metadata")]
        assert Counter((stage, model, code) for stage, model, _, code, _ in provider_http) == Counter(
            [(stage, model, 404 if model == failed_model else 200)
             for stage in ("stage1", "stage2") for model in FREE_MODELS]
            + [("stage3", FREE_CHAIRMAN, 200)])
        assert [stage for stage, *_ in provider_http] == ["stage1"] * 3 + ["stage2"] * 3 + ["stage3"]
        assert all(host == "openrouter.ai" for _, _, host, _, _ in provider_http)
        assert len(stage1) == 2 and {r["model"]: r["response"] for r in stage1} == available
        assert len(stage2) == 2 and {r["model"] for r in stage2} == set(available)
        assert all(r["parsed_ranking"]["ranking"] == LABELS[:2] for r in stage2)
        assert {item["model"] for item in metadata["label_to_model"].values()} == set(available)
        assert len(metadata["aggregate_rankings"]) == 2
        assert {r["model"] for r in metadata["aggregate_rankings"]} == set(available)
        assert all(r["vote_count"] == 1 for r in metadata["aggregate_rankings"])
        assert stage3["model"] == FREE_CHAIRMAN and stage3["response"] == FINAL_TEXT
        assert not stage3.get("error_status")
        assert metadata["degraded_mode"] == "two_models"
        assert metadata["config"]["council_size"] == 3
        assert metadata["config"]["responses_received"] == 2
        assert metadata["peer_review"]["state"] == "completed"
        assert metadata["peer_review"]["effective_votes"] == 2
        assert metadata["usage"]["total"]["total_tokens"] == 5 * USAGE["total_tokens"]
        assert metadata["usage"]["total"]["cost_usd"] == 0
        assert [context for stage, *_, context in provider_http if stage == "stage3"] == [True]
        persisted = json.loads((tmp_path / "conversations" / f"{cid}.json").read_text(encoding="utf-8"))
        assert persisted["messages"] == [
            {"role": "user", "content": QUESTION},
            {"role": "assistant", "stage1": stage1, "stage2": stage2, "stage3": stage3, "metadata": metadata},
        ]
        reloaded = client.get(f"/api/conversations/{cid}")
        assert reloaded.status_code == 200 and reloaded.json()["messages"] == persisted["messages"]
        assert metadata.get("status") == "partial"


@pytest.mark.parametrize("provider_http", ["all"], indirect=True)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_eng03_all_models_unavailable(provider_http, tmp_path, monkeypatch, stream, caplog):
    """All provider 404s must stop deliberation and persist an explicit failure."""
    from fastapi.testclient import TestClient
    from backend import main, storage

    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = client.post(endpoint, json={"content": QUESTION})
        assert result.status_code == 200, result.text
        if stream:
            assert result.headers["content-type"].startswith("text/event-stream")
            events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
            assert [e["type"] for e in events] == ["engine_progress"] * 2 + [
                "stage1_complete", "stage2_complete", "stage3_complete", "complete"]
            progress = [e["data"] for e in events[:2]]
            assert [(e["stage"], e["state"]) for e in progress] == [("stage1", "started"), ("stage1", "failed")]
            assert progress[1]["responses"] == 0
            stage1, stage2, stage3 = [e["data"] for e in events[2:5]]
            metadata = events[5]["metadata"]
            assert events[3]["metadata"] == events[4]["metadata"] == metadata
            assert metadata["ui_runtime_progress"] == progress
        else:
            payload = result.json()
            stage1, stage2, stage3, metadata = [payload[key] for key in ("stage1", "stage2", "stage3", "metadata")]
        assert Counter(provider_http) == Counter(("stage1", model, "openrouter.ai", 404, None) for model in FREE_MODELS)
        assert stage1 == stage2 == []
        assert stage3["model"] == "error" and stage3["error_status"] == "no_responses"
        assert "No Council models returned" in stage3["response"]
        assert FINAL_TEXT not in stage3["response"]
        assert metadata["status"] == "failed"
        assert metadata["peer_review"]["state"] == "not_run"
        assert metadata["peer_review"]["effective_votes"] == 0
        assert not metadata.get("aggregate_rankings")
        assert not metadata.get("verdict") and metadata.get("quality_metrics") is None
        assert all(metadata["usage"][stage]["total_tokens"] == 0 for stage in ("stage1", "stage1_5", "stage2", "stage3"))
        assert "zero usable model responses" in caplog.text
        for model in FREE_MODELS:
            assert any(model in record.getMessage() and "404" in record.getMessage() for record in caplog.records)
        assert "eng-01-test-only-not-a-real-key" not in caplog.text
        persisted = json.loads((tmp_path / "conversations" / f"{cid}.json").read_text(encoding="utf-8"))
        assert persisted["messages"] == [
            {"role": "user", "content": QUESTION},
            {"role": "assistant", "stage1": [], "stage2": [], "stage3": stage3, "metadata": metadata},
        ]
        reloaded = client.get(f"/api/conversations/{cid}")
        assert reloaded.status_code == 200 and reloaded.json()["messages"] == persisted["messages"]


@pytest.mark.parametrize("provider_http", ["rate-stage1-member", "rate-stage1-chairman", "rate-stage3-chairman"], indirect=True)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_eng04_single_model_rate_limited(provider_http, request, tmp_path, monkeypatch, stream):
    """Respect the 120-second model hint by not calling that model again in this round."""
    from fastapi.testclient import TestClient
    from backend import main, storage

    scenario = request.node.callspec.params["provider_http"]
    stage1_limited = scenario.startswith("rate-stage1")
    limited = FREE_MODELS[-1] if scenario == "rate-stage1-member" else FREE_CHAIRMAN
    available = {model: answer for model, answer in ANSWERS.items() if not stage1_limited or model != limited}
    chairman = FREE_CHAIRMAN if limited != FREE_CHAIRMAN else FREE_MODELS[1]
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = client.post(endpoint, json={"content": QUESTION})
        assert result.status_code == 200, result.text
        if stream:
            assert result.headers["content-type"].startswith("text/event-stream")
            events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
            assert [e["type"] for e in events[-4:]] == ["stage1_complete", "stage2_complete", "stage3_complete", "complete"]
            assert all(e["type"] == "engine_progress" for e in events[:-4])
            progress = [e["data"] for e in events[:-4]]
            stage1, stage2, stage3 = [e["data"] for e in events[-4:-1]]
            metadata = events[-1]["metadata"]
            assert events[-3]["metadata"] == events[-2]["metadata"] == metadata
            assert metadata["ui_runtime_progress"] == progress
        else:
            payload = result.json()
            stage1, stage2, stage3, metadata = [payload[key] for key in ("stage1", "stage2", "stage3", "metadata")]
        expected = [("stage1", model, 429 if stage1_limited and model == limited else 200) for model in FREE_MODELS]
        expected += [("stage2", model, 200) for model in available]
        if not stage1_limited:
            expected += [("stage3", FREE_CHAIRMAN, 429)]
        expected += [("stage3", chairman, 200)]
        assert Counter((stage, model, code) for stage, model, _, code, _ in provider_http) == Counter(expected)
        assert [stage for stage, *_ in provider_http] == [stage for stage, _, _ in expected]
        assert all(host == "openrouter.ai" for _, _, host, _, _ in provider_http)
        failure = next(i for i, entry in enumerate(provider_http) if entry[3] == 429)
        assert all(entry[1] != limited for entry in provider_http[failure + 1:])
        assert {r["model"]: r["response"] for r in stage1} == available
        assert len(stage2) == len(available) and {r["model"] for r in stage2} == set(available)
        assert all(r["parsed_ranking"]["ranking"] == LABELS[:len(available)] for r in stage2)
        assert {item["model"] for item in metadata["label_to_model"].values()} == set(available)
        assert all(r["vote_count"] == len(available) - 1 for r in metadata["aggregate_rankings"])
        assert stage3["model"] == chairman and stage3["response"] == FINAL_TEXT and not stage3.get("error_status")
        assert metadata["config"]["council_size"] == 3 and metadata["config"]["responses_received"] == len(available)
        assert metadata["peer_review"]["state"] == "completed"
        assert metadata["peer_review"]["effective_votes"] == len(available) * (len(available) - 1)
        if stage1_limited:
            assert metadata["status"] == "partial"
            assert metadata["stage1_rate_limits"] == {limited: 120}
        else:
            assert metadata.get("status") not in ("failed", "partial")
        assert metadata["usage"]["total"]["total_tokens"] == (2 * len(available) + 1) * USAGE["total_tokens"]
        assert metadata["usage"]["total"]["cost_usd"] == 0
        assert all(context is True for stage, _, _, _, context in provider_http if stage == "stage3")
        if stream:
            expected_progress = [(stage, state) for stage in ("stage1", "stage1_5", "stage2", "stage3") for state in ("started", "completed")]
            if limited == FREE_CHAIRMAN:
                expected_progress.insert(-1, ("stage3", "retrying"))
                assert progress[-2]["model"] == chairman
            assert [(e["stage"], e["state"]) for e in progress] == expected_progress
            assert progress[1]["responses"] == len(available)
            if stage1_limited:
                assert progress[1]["rate_limits"] == {limited: 120}
        persisted = json.loads((tmp_path / "conversations" / f"{cid}.json").read_text(encoding="utf-8"))
        assert persisted["messages"] == [
            {"role": "user", "content": QUESTION},
            {"role": "assistant", "stage1": stage1, "stage2": stage2, "stage3": stage3, "metadata": metadata},
        ]
        reloaded = client.get(f"/api/conversations/{cid}")
        assert reloaded.status_code == 200 and reloaded.json()["messages"] == persisted["messages"]
    assert council._get_chairman_model() == FREE_CHAIRMAN


@pytest.mark.parametrize("provider_http", ["rate-stage1-member"], indirect=True)
def test_eng04_native_retry_after_header(provider_http):
    from llm_council.openrouter import query_model_with_status

    model = FREE_MODELS[-1]
    status = asyncio.run(query_model_with_status(model, [{"role": "user", "content": QUESTION}]))
    assert status["status"] == "rate_limited" and status["retry_after"] == 120
    assert provider_http == [("stage1", model, "openrouter.ai", 429, None)]


@pytest.mark.parametrize("provider_http", ["rate-stage2-member", "rate-stage2-chairman"], indirect=True)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_eng04_review_rate_limit(provider_http, request, tmp_path, monkeypatch, stream):
    """A missing review is partial, not a full review; use available Chairman."""
    from fastapi.testclient import TestClient
    from backend import main, storage

    limited = FREE_CHAIRMAN if request.node.callspec.params["provider_http"] == "rate-stage2-chairman" else FREE_MODELS[-1]
    chairman = FREE_MODELS[1] if limited == FREE_CHAIRMAN else FREE_CHAIRMAN
    reviewers = set(FREE_MODELS) - {limited}
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = client.post(endpoint, json={"content": QUESTION})
        assert result.status_code == 200, result.text
        if stream:
            assert result.headers["content-type"].startswith("text/event-stream")
            events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
            assert [e["type"] for e in events[-4:]] == ["stage1_complete", "stage2_complete", "stage3_complete", "complete"]
            assert all(e["type"] == "engine_progress" for e in events[:-4])
            progress = [e["data"] for e in events[:-4]]
            stage1, stage2, stage3 = [e["data"] for e in events[-4:-1]]
            metadata = events[-1]["metadata"]
            assert events[-3]["metadata"] == events[-2]["metadata"] == metadata
            assert metadata["ui_runtime_progress"] == progress
        else:
            payload = result.json()
            stage1, stage2, stage3, metadata = [payload[key] for key in ("stage1", "stage2", "stage3", "metadata")]
        expected = [("stage1", model, 200) for model in FREE_MODELS]
        expected += [("stage2", model, 429 if model == limited else 200) for model in FREE_MODELS]
        expected += [("stage3", chairman, 200)]
        assert Counter((stage, model, code) for stage, model, _, code, _ in provider_http) == Counter(expected)
        assert [stage for stage, *_ in provider_http] == [stage for stage, _, _ in expected]
        assert all(host == "openrouter.ai" for _, _, host, _, _ in provider_http)
        failure = next(i for i, entry in enumerate(provider_http) if entry[3] == 429)
        assert all(entry[1] != limited for entry in provider_http[failure + 1:])
        assert {r["model"]: r["response"] for r in stage1} == ANSWERS
        assert len(stage2) == 2 and {r["model"] for r in stage2} == reviewers
        assert all(r["parsed_ranking"]["ranking"] == LABELS for r in stage2)
        assert {item["model"] for item in metadata["label_to_model"].values()} == set(FREE_MODELS)
        assert {r["model"]: r["vote_count"] for r in metadata["aggregate_rankings"]} == {model: 2 if model == limited else 1 for model in FREE_MODELS}
        assert stage3["model"] == chairman and stage3["response"] == FINAL_TEXT and not stage3.get("error_status")
        assert metadata["config"]["council_size"] == metadata["config"]["responses_received"] == 3
        assert metadata["peer_review"]["state"] == "partial"
        assert metadata["peer_review"]["missing_reviewers"] == [limited]
        assert metadata["peer_review"]["effective_votes"] == 4
        assert metadata["status"] == "partial"
        assert metadata["usage"]["total"]["total_tokens"] == 6 * USAGE["total_tokens"]
        assert metadata["usage"]["total"]["cost_usd"] == 0
        assert [context for stage, _, _, _, context in provider_http if stage == "stage3"] == [True]
        if stream:
            expected_progress = [("stage1", "started"), ("stage1", "completed"), ("stage1_5", "started"), ("stage1_5", "completed"), ("stage2", "started"), ("stage2", "partial"), ("stage3", "started"), ("stage3", "completed")]
            if limited == FREE_CHAIRMAN:
                expected_progress.insert(-1, ("stage3", "retrying"))
                assert progress[-2]["model"] == chairman
            assert [(e["stage"], e["state"]) for e in progress] == expected_progress
            assert progress[5]["missing_reviewers"] == [limited] and progress[5]["valid_reviews"] == 2
        persisted = json.loads((tmp_path / "conversations" / f"{cid}.json").read_text(encoding="utf-8"))
        assert persisted["messages"] == [
            {"role": "user", "content": QUESTION},
            {"role": "assistant", "stage1": stage1, "stage2": stage2, "stage3": stage3, "metadata": metadata},
        ]
        reloaded = client.get(f"/api/conversations/{cid}")
        assert reloaded.status_code == 200 and reloaded.json()["messages"] == persisted["messages"]
    assert council._get_chairman_model() == FREE_CHAIRMAN


@pytest.mark.parametrize("provider_http", ["review-cap"], indirect=True)
def test_eng04_intentional_reviewer_cap_is_not_a_failure(provider_http):
    """A native sampling limit is not a missing HTTP response."""
    from backend import main

    stage1, stage2, stage3, metadata = asyncio.run(main._run_council_with_free_chairman_recovery(
        QUESTION, free_mode=True,
    ))
    assert len(stage1) == 3 and len(stage2) == 2
    assert len(provider_http) == 6 and all(entry[3] == 200 for entry in provider_http)
    assert stage3["model"] == FREE_CHAIRMAN and stage3["response"] == FINAL_TEXT
    assert metadata["peer_review"]["state"] == "completed"
    assert not metadata["peer_review"].get("missing_reviewers")
    assert metadata.get("status") not in ("failed", "partial")


@pytest.mark.parametrize("provider_http", ["rate-stage1-member", "rate-stage2-chairman"], indirect=True)
def test_eng04_round_state_is_reset_in_same_task(provider_http):
    from backend import main

    async def drive():
        _, _, _, metadata = await main._run_council_with_free_chairman_recovery(QUESTION, free_mode=True)
        assert metadata["status"] == "partial"
        assert metadata.get("stage1_rate_limits") or metadata["peer_review"].get("missing_reviewers")
        assert not engine_runtime.stage1_rate_limits()
        assert engine_runtime._missing_reviewers.get() == ()
        assert not engine_runtime._free_chairman_retry.get()

    asyncio.run(drive())



def test_eng04_cancel_stage1_closes_all_provider_requests(provider_http, monkeypatch):
    """The Free status adapter must preserve cancellation of in-flight native HTTP calls."""
    from backend import main

    async def drive():
        active = set()
        cancelled = set()
        tasks = []
        all_started = asyncio.Event()
        all_cancelled = asyncio.Event()
        release = asyncio.Event()

        async def blocked(_transport, request):
            assert request.method == "POST"
            assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
            model = json.loads(request.content)["model"]
            assert model in FREE_MODELS
            assert model not in active
            active.add(model)
            tasks.append(asyncio.current_task())
            if active == set(FREE_MODELS):
                all_started.set()
            try:
                await release.wait()
                pytest.fail("Cancelled HTTP call should not finish normally")
            except asyncio.CancelledError:
                cancelled.add(model)
                if cancelled == set(FREE_MODELS):
                    all_cancelled.set()
                raise

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
        task = asyncio.create_task(main._run_council_with_free_chairman_recovery(QUESTION, free_mode=True))
        try:
            await asyncio.wait_for(all_started.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(all_cancelled.wait(), 1)
            assert cancelled == set(FREE_MODELS)
        finally:
            # Even a failing regression must not leave tasks running in the suite.
            for child in tasks:
                child.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(drive())


@pytest.mark.parametrize("provider_http", ["quota-stage1", "quota-stage2", "quota-stage3"], indirect=True)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_eng05_account_quota(provider_http, request, tmp_path, monkeypatch, stream):
    """The native status API loses quota scope; avoid claiming certainty or useless rotation."""
    from fastapi.testclient import TestClient
    from backend import main, storage

    onset = request.node.callspec.params["provider_http"]
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = client.post(endpoint, json={"content": QUESTION})
        assert result.status_code == 200, result.text
        if stream:
            assert result.headers["content-type"].startswith("text/event-stream")
            events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
            assert [e["type"] for e in events[-4:]] == ["stage1_complete", "stage2_complete", "stage3_complete", "complete"]
            assert all(e["type"] == "engine_progress" for e in events[:-4])
            progress = [e["data"] for e in events[:-4]]
            stage1, stage2, stage3 = [e["data"] for e in events[-4:-1]]
            metadata = events[-1]["metadata"]
            assert events[-3]["metadata"] == events[-2]["metadata"] == metadata
            assert metadata["ui_runtime_progress"] == progress
        else:
            payload = result.json()
            stage1, stage2, stage3, metadata = [payload[key] for key in ("stage1", "stage2", "stage3", "metadata")]
        expected = [("stage1", model, 429 if onset == "quota-stage1" else 200) for model in FREE_MODELS]
        if onset != "quota-stage1":
            expected += [("stage2", model, 429 if onset == "quota-stage2" else 200) for model in FREE_MODELS]
            expected += [("stage3", model, 429) for model in ([FREE_CHAIRMAN] if onset == "quota-stage2" else FREE_MODELS)]
        assert Counter((stage, model, code) for stage, model, _, code, _ in provider_http) == Counter(expected)
        assert [stage for stage, *_ in provider_http] == [stage for stage, _, _ in expected]
        assert all(host == "openrouter.ai" for _, _, host, _, _ in provider_http)
        assert metadata["status"] == "failed"
        assert not metadata.get("verdict") and metadata.get("quality_metrics") is None
        assert FINAL_TEXT not in stage3["response"]
        assert "account" in stage3["response"].lower()
        assert "may" in stage3["response"].lower() or "cannot" in stage3["response"].lower()
        if onset == "quota-stage1":
            assert stage1 == stage2 == []
            assert stage3["error_status"] == "no_responses"
            assert metadata["stage1_rate_limits"] == dict.fromkeys(FREE_MODELS, 3600)
            assert metadata["peer_review"]["state"] == "not_run"
        else:
            assert {r["model"]: r["response"] for r in stage1} == ANSWERS
            assert stage3["error_status"] == "rate_limited"
            if onset == "quota-stage2":
                assert stage2 == [] and metadata["peer_review"]["state"] == "failed"
                assert stage3["model"] == FREE_CHAIRMAN
                assert stage3["free_fallback_suppressed"] == "all_reviews_missing"
            else:
                assert len(stage2) == 3 and metadata["peer_review"]["state"] == "completed"
                assert stage3["free_fallback_exhausted"] is True
                assert [r["model"] for r in stage3["free_chairman_attempts"]] == list(FREE_MODELS)
        if stream:
            if onset == "quota-stage1":
                assert [(e["stage"], e["state"]) for e in progress] == [("stage1", "started"), ("stage1", "failed")]
            else:
                assert progress[-1]["stage"] == "stage3" and progress[-1]["state"] == "failed"
                assert sum(e["state"] == "retrying" for e in progress) == (2 if onset == "quota-stage3" else 0)
        persisted = json.loads((tmp_path / "conversations" / f"{cid}.json").read_text(encoding="utf-8"))
        assert persisted["messages"] == [
            {"role": "user", "content": QUESTION},
            {"role": "assistant", "stage1": stage1, "stage2": stage2, "stage3": stage3, "metadata": metadata},
        ]
        reloaded = client.get(f"/api/conversations/{cid}")
        assert reloaded.status_code == 200 and reloaded.json()["messages"] == persisted["messages"]
    assert council._get_chairman_model() == FREE_CHAIRMAN


@pytest.mark.parametrize("provider_http", [
    f"infra-{stage}-{failure}" for stage in ("stage1", "stage2", "stage3")
    for failure in ("timeout", "connect", "503")
] + ["infra-stage3-all503"], indirect=True)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_eng06_infrastructure_failure(provider_http, request, tmp_path, monkeypatch, stream):
    """Actual provider failures, bounded attempts and factual terminal state."""
    from fastapi.testclient import TestClient
    from backend import main, storage

    _, onset, failure = request.node.callspec.params["provider_http"].split("-")
    member = FREE_MODELS[-1]
    code = 503 if failure in ("503", "all503") else failure
    available = {model: answer for model, answer in ANSWERS.items() if onset != "stage1" or model != member}
    reviewers = set(FREE_MODELS) - ({member} if onset in ("stage1", "stage2") else set())
    success = onset != "stage3" or failure == "503"
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = client.post(endpoint, json={"content": QUESTION})
        assert result.status_code == 200, result.text
        if stream:
            assert result.headers["content-type"].startswith("text/event-stream")
            events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
            assert [e["type"] for e in events[-4:]] == ["stage1_complete", "stage2_complete", "stage3_complete", "complete"]
            assert all(e["type"] == "engine_progress" for e in events[:-4])
            progress = [e["data"] for e in events[:-4]]
            stage1, stage2, stage3 = [e["data"] for e in events[-4:-1]]
            metadata = events[-1]["metadata"]
            assert events[-3]["metadata"] == events[-2]["metadata"] == metadata
            assert metadata["ui_runtime_progress"] == progress
        else:
            payload = result.json()
            stage1, stage2, stage3, metadata = [payload[key] for key in ("stage1", "stage2", "stage3", "metadata")]
        expected = [("stage1", model, code if onset == "stage1" and model == member else 200) for model in FREE_MODELS]
        expected += [("stage2", model, code if onset in ("stage1", "stage2") and model == member else 200) for model in FREE_MODELS]
        expected += [("stage3", FREE_CHAIRMAN, code if onset == "stage3" else 200)]
        if onset == "stage3" and failure == "503":
            expected += [("stage3", FREE_MODELS[1], 200)]
        elif failure == "all503":
            expected += [("stage3", model, 503) for model in FREE_MODELS[1:]]
        assert Counter((stage, model, status) for stage, model, _, status, _ in provider_http) == Counter(expected)
        assert [stage for stage, *_ in provider_http] == [stage for stage, _, _ in expected]
        assert all(host == "openrouter.ai" for _, _, host, _, _ in provider_http)
        assert {r["model"]: r["response"] for r in stage1} == available
        assert {r["model"] for r in stage2} == reviewers
        assert all(r["parsed_ranking"]["ranking"] == LABELS[:len(available)] for r in stage2)
        assert {item["model"] for item in metadata["label_to_model"].values()} == set(available)
        assert metadata["peer_review"]["state"] == ("partial" if onset == "stage2" else "completed")
        assert metadata["config"]["responses_received"] == len(available)
        assert metadata["usage"]["total"]["total_tokens"] == sum(status == 200 for _, _, status in expected) * USAGE["total_tokens"]
        if success:
            assert stage3["response"] == FINAL_TEXT and not stage3.get("error_status")
            assert stage3["model"] == (FREE_MODELS[1] if onset == "stage3" else FREE_CHAIRMAN)
            if onset != "stage3":
                assert metadata["status"] == "partial"
            else:
                assert metadata.get("status") not in ("failed", "partial")
        else:
            assert stage3["error_status"] == ("timeout" if failure == "timeout" else "error")
            assert FINAL_TEXT not in stage3["response"]
            assert metadata["status"] == "failed"
            assert not metadata.get("verdict") and metadata.get("quality_metrics") is None
            if failure == "all503":
                assert stage3["free_fallback_exhausted"] is True
                assert [r["model"] for r in stage3["free_chairman_attempts"]] == list(FREE_MODELS)
            else:
                assert stage3["model"] == FREE_CHAIRMAN
                assert ("Timeout" if failure == "timeout" else "ConnectError") in stage3["error_detail"]
        if stream:
            assert progress[-1]["stage"] == "stage3"
            assert progress[-1]["state"] == ("completed" if success else "failed")
            assert sum(e["state"] == "retrying" for e in progress) == (2 if failure == "all503" else 1 if onset == "stage3" and failure == "503" else 0)
            assert progress[1]["responses"] == len(available)
        persisted = json.loads((tmp_path / "conversations" / f"{cid}.json").read_text(encoding="utf-8"))
        assert persisted["messages"] == [
            {"role": "user", "content": QUESTION},
            {"role": "assistant", "stage1": stage1, "stage2": stage2, "stage3": stage3, "metadata": metadata},
        ]
        reloaded = client.get(f"/api/conversations/{cid}")
        assert reloaded.status_code == 200 and reloaded.json()["messages"] == persisted["messages"]


def test_eng06_native_wall_clock_timeout(provider_http, monkeypatch):
    """A stalled HTTP transport must actually be cancelled by native elapsed timeout."""
    from llm_council.gateway_adapter import query_model_with_status

    async def drive():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def stalled(_transport, request):
            assert request.url.host == "openrouter.ai"
            assert json.loads(request.content)["model"] == FREE_CHAIRMAN
            assert request.extensions["timeout"]["read"] == 0.05
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", stalled)
        result = await asyncio.wait_for(query_model_with_status(
            FREE_CHAIRMAN, [{"role": "user", "content": QUESTION}], timeout=0.05,
        ), timeout=2.0)
        assert started.is_set() and cancelled.is_set()
        assert result["status"] == "timeout" and "0.05" in result["error"]

    asyncio.run(drive())
