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
    config_path = tmp_path / "eng01.yaml"
    config_path.write_text(yaml.safe_dump({
        "tiers": {"default": "high"},
        "council": {"models": list(FREE_MODELS), "chairman": FREE_CHAIRMAN,
                    "synthesis_mode": "consensus", "exclude_self_votes": True,
                    "style_normalization": False},
        "gateways": {"default": "openrouter", "fallback": {"enabled": False}},
        "evaluation": {"rubric": {"enabled": False}, "safety": {"enabled": False},
                       "bias": {"audit_enabled": False, "persistence_enabled": False}},
        "cache": {"enabled": False, "ttl_seconds": 0},
    }), encoding="utf-8")
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(config_path))
    reload_config()
    calls = []
    failed_model = getattr(request, "param", None)
    available = {model: answer for model, answer in ANSWERS.items() if model != failed_model}
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
            if failed_model is not None:
                assert ANSWERS[failed_model] not in prompt
        elif prompt.startswith("You are the Chairman of an LLM Council."):
            stage, content = "stage3", FINAL_TEXT
            assert model == FREE_CHAIRMAN
            assert QUESTION in prompt and all(answer in prompt for answer in available.values())
            if failed_model is not None:
                assert ANSWERS[failed_model] not in prompt
            chairman_context_matches = "AGGREGATE RANKINGS" in prompt and review_text in prompt
        else:
            pytest.fail("Unexpected provider prompt (no credentials or prompt text logged)")
        # Record no credentials or raw prompts. Native HTTP client parses the response.
        if model == failed_model:
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
