"""Native profile acceptance through the actual 0.53.0 compatibility API."""
import json
import os
import socket
from collections import Counter

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from llm_council import council
from llm_council.unified_config import get_config, reload_config

# Independent expectations from the pinned package's models/default_pools.yaml,
# tier_contract.py and TimeoutsConfig. Do not derive them from app settings.
PROFILES = {
    "quick": (['google/gemini-3.5-flash-lite', 'deepseek/deepseek-v4-flash',
               'anthropic/claude-haiku-4.5'], 'anthropic/claude-haiku-4.5', 20, 30),
    "balanced": (['openai/gpt-5.6-luna', 'anthropic/claude-sonnet-5',
                  'deepseek/deepseek-v4-flash', 'google/gemini-3.7-flash'],
                 'anthropic/claude-sonnet-5', 45, 90),
    "high": (['openai/gpt-5.6-sol', 'anthropic/claude-opus-5',
              'deepseek/deepseek-v4-pro-0813', 'z-ai/glm-5.3'],
             'openai/gpt-5.6-sol', 90, 180),
    "reasoning": (['openai/gpt-5.6-sol-pro', 'anthropic/claude-opus-5',
                   'z-ai/glm-5.3', 'deepseek/deepseek-v4-pro-0813'],
                  'anthropic/claude-opus-5', 300, 600),
}
KEY = "eng13-test-only-not-a-real-key"
TITLE_MODEL = "google/gemini-2.5-flash"
TITLE = "Archive Retention Decision"
QUESTION = "ENG13: Should we preserve the original archive?"
FINAL = "Preserve the original archive and verify its integrity."


@pytest.fixture
def isolated_profile_config(tmp_path, monkeypatch):
    from backend import config, storage

    for name in tuple(os.environ):
        if name.startswith(("LLM_COUNCIL_", "OTEL_", "POSTHOG_")) or name.lower().endswith("_proxy"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setenv("LLM_COUNCIL_PERFORMANCE_TRACKING", "false")
    monkeypatch.setenv("LLM_COUNCIL_EARLY_CONSENSUS", "false")
    monkeypatch.setenv("LLM_COUNCIL_PERFORMANCE_STORE", str(tmp_path / "performance.jsonl"))
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump({
        "tiers": {"default": "high"},
        "gateways": {"default": "openrouter", "fallback": {"enabled": True}},
        "evaluation": {"bias": {"persistence_enabled": False}},
    }), encoding="utf-8")
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(path))
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    reload_config()
    return path


def candidate(models, model):
    return f"Candidate {models.index(model) + 1}: preserve the original archive."


def review_text(models):
    labels = [f"Response {chr(65 + i)}" for i in range(len(models))]
    return "All candidates support archive retention.\n```json\n" + json.dumps({
        "ranking": labels, "scores": {label: 9 - i for i, label in enumerate(labels)},
    }) + "\n```"


def install_provider(monkeypatch, models, chairman, stage_timeout):
    calls = []

    def deny_network(*args, **kwargs):
        pytest.fail("Unexpected external network in native profile acceptance")

    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    original_connect = socket.socket.connect

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            deny_network()
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)

    async def handle(_transport, request):
        assert request.method == "POST"
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers.get("authorization") == f"Bearer {KEY}"
        body = json.loads(request.content)
        model, messages = body["model"], body["messages"]
        prompt = messages[-1]["content"]
        assert QUESTION in prompt
        if prompt.startswith("Generate a very short title"):
            stage, timeout, content = "title", 30.0, TITLE
            assert model == TITLE_MODEL
        elif prompt == QUESTION:
            stage, timeout = "stage1", 120.0
            assert model in models
            content = candidate(models, model)
        elif "<responses_to_evaluate>" in prompt:
            stage, timeout, content = "stage2", stage_timeout, review_text(models)
            assert model in models
            assert all(candidate(models, m) in prompt for m in models)
        elif prompt.startswith("You are the Chairman of an LLM Council."):
            stage, timeout, content = "stage3", stage_timeout, FINAL
            assert model == chairman
            assert all(candidate(models, m) in prompt for m in models)
            assert review_text(models) in prompt and "AGGREGATE RANKINGS" in prompt
        else:
            pytest.fail(f"Unexpected provider prompt: {prompt[:120]}")
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), timeout)
        calls.append((stage, model))
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0125},
        })

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return calls


def assert_profile(payload, profile, multiplier):
    models, chairman, per_model, total = PROFILES[profile]
    assert payload["profile"] == profile
    assert payload["models"] == models and payload["use_profile_models"] is True
    assert payload["resolved_chairman"] == chairman
    assert payload["timeout_multiplier"] == multiplier
    for name, (expected_models, expected_chairman, _, _) in PROFILES.items():
        assert payload["options"]["profiles"][name] == {"models": expected_models, "chairman": expected_chairman}
    cfg = get_config()
    assert cfg.tiers.default == profile and cfg.council.models == models
    assert not cfg.council.chairman  # native implicit tier resolution, not stale custom chairman
    assert council._get_chairman_model() == chairman
    assert cfg.timeouts.get_timeout(profile, "per_model") == per_model * multiplier * 1000
    assert cfg.timeouts.get_timeout(profile, "total") == total * multiplier * 1000


def decode_response(response, stream):
    assert response.status_code == 200, response.text
    if not stream:
        return response.json()
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [event["type"] for event in events[-5:]] == [
        "stage1_complete", "stage2_complete", "stage3_complete", "complete", "title_complete"]
    assert events[-1]["data"] == {"title": TITLE}
    assert all(e["type"] == "engine_progress" for e in events[:-5])
    progress = [e["data"] for e in events[:-5]]
    assert [(e["stage"], e["state"]) for e in progress] == [
        (stage, state) for stage in ("stage1", "stage1_5", "stage2", "stage3")
        for state in ("started", "completed")]
    metadata = events[-2]["metadata"]
    assert events[-4]["metadata"] == events[-3]["metadata"] == metadata
    assert metadata["ui_runtime_progress"] == progress
    return {"stage1": events[-5]["data"], "stage2": events[-4]["data"],
            "stage3": events[-3]["data"], "metadata": metadata}


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
@pytest.mark.parametrize("multiplier", [1, 7], ids=["default-timeout", "raised-timeout"])
def test_native_profile_actual_round(isolated_profile_config, monkeypatch, profile, stream, multiplier):
    from backend import main, storage

    assert main.run_full_council is council.run_full_council
    models, chairman, per_model, _ = PROFILES[profile]
    # Pinned compatibility API has a 120s floor for Stage 2/3; Stage 1 keeps
    # the native query_models_parallel default. This is not verify()'s global deadline.
    calls = install_provider(monkeypatch, models, chairman, max(120.0, per_model * multiplier))
    with TestClient(main.app) as client:
        prior = "quick" if profile != "quick" else "reasoning"
        # Seed stale non-default model and chairman through the actual API.
        result = client.put("/api/settings", json={"profile": prior, "use_profile_models": False,
                            "models": ["openai/stale-model"], "chairman": "openai/stale-chairman"})
        assert result.status_code == 200, result.text
        assert get_config().council.chairman == "openai/stale-chairman"
        if stream:
            result = client.put("/api/settings", json={"profile": "free"})
            assert result.status_code == 200 and result.json()["profile"] == "free"
        result = client.put("/api/settings", json={"profile": profile, "timeout_multiplier": multiplier})
        assert result.status_code == 200, result.text
        assert_profile(result.json(), profile, multiplier)
        saved = yaml.safe_load(isolated_profile_config.read_text(encoding="utf-8"))
        assert saved["tiers"]["default"] == profile
        assert saved["council"]["models"] == models and "chairman" not in saved["council"]
        assert saved["timeouts"]["multiplier"] == multiplier
        reload_config()
        assert_profile(client.get("/api/settings").json(), profile, multiplier)
        assert not calls, "Settings must not call models"
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        path = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        payload = decode_response(client.post(path, json={"content": QUESTION}), stream)
        assert Counter(calls) == Counter([("title", TITLE_MODEL)] + [("stage1", m) for m in models]
                                       + [("stage2", m) for m in models] + [("stage3", chairman)])
        assert [stage for stage, _ in calls if stage != "title"] == ["stage1"] * len(models) + ["stage2"] * len(models) + ["stage3"]
        assert {r["model"]: r["response"] for r in payload["stage1"]} == {m: candidate(models, m) for m in models}
        assert len(payload["stage1"]) == len(models)
        assert {r["model"] for r in payload["stage2"]} == set(models)
        assert len(payload["stage2"]) == len(models)
        assert all(r["ranking"] == review_text(models) for r in payload["stage2"])
        assert all(r["parsed_ranking"]["ranking"] == [f"Response {chr(65 + i)}" for i in range(len(models))] for r in payload["stage2"])
        assert payload["stage3"]["model"] == chairman and payload["stage3"]["response"] == FINAL
        assert not payload["stage3"].get("error_status")
        metadata = payload["metadata"]
        assert metadata.get("status") not in ("failed", "partial") and not metadata.get("degraded_mode")
        assert metadata["config"]["chairman"] == chairman
        assert metadata["config"]["council_size"] == metadata["config"]["responses_received"] == len(models)
        assert {entry["model"] for entry in metadata["label_to_model"].values()} == set(models)
        assert {entry["model"] for entry in metadata["aggregate_rankings"]} == set(models)
        assert all(entry["vote_count"] == len(models) - 1 for entry in metadata["aggregate_rankings"])
        assert metadata["peer_review"]["state"] == "completed"
        assert metadata["peer_review"]["effective_votes"] == len(models) * (len(models) - 1)
        total = metadata["usage"]["total"]
        assert total["total_tokens"] == 18 * (2 * len(models) + 1)
        assert total["cost_known"] is True and total["cost_usd"] == pytest.approx(0.0125 * (2 * len(models) + 1))
        disk = storage.get_conversation(cid)
        assert disk["title"] == TITLE
        assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **payload}]
        assert client.get(f"/api/conversations/{cid}").json() == disk
        assert_profile(client.get("/api/settings").json(), profile, multiplier)
