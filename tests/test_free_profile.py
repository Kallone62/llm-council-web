"""Offline regression tests for the OpenRouter-only Free onboarding profile."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend import main, storage, conversation_context
from backend.free_profile import (
    FREE_MODELS, FREE_CHAIRMAN, FREE_SUMMARY_MODEL,
    LEGACY_FREE_MODELS, LEGACY_FREE_CHAIRMAN,
    is_legacy_free_document, is_free_document,
)
from llm_council.unified_config import get_config, reload_config


@pytest.fixture()
def free_client(tmp_path, monkeypatch):
    from backend import config as app_config

    path = tmp_path / "llm_council.yaml"
    original = __import__("os").environ.get("LLM_COUNCIL_CONFIG")
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(path))
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    # Always begin from a minimal native engine config to avoid surprises from
    # a developer's locally customized defaults.
    path.write_text("tiers:\n  default: high\n", encoding="utf-8")
    reload_config()
    client = TestClient(main.app)
    yield client, path
    if original is None:
        monkeypatch.delenv("LLM_COUNCIL_CONFIG", raising=False)
    else:
        monkeypatch.setenv("LLM_COUNCIL_CONFIG", original)
    reload_config()


def _select_free(client):
    r = client.put("/api/settings", json={
        "profile": "free",
        "use_profile_models": True,
        "chairman": None,
        "style_normalization": "off",
        "rubric_enabled": False,
        "bias_audit_enabled": False,
        "safety_enabled": False,
    })
    assert r.status_code == 200, r.text
    return r.json()


def _answer(answer="Free answer"):
    return (
        [{"model": FREE_MODELS[0], "response": "Opinion 1"}],
        [{"model": FREE_MODELS[1], "ranking": "Opinion 1"}],
        {"model": FREE_CHAIRMAN, "response": answer},
        {"usage": {"total": {"total_tokens": 10}}},
    )


def test_free_is_fifth_profile_without_modifying_native_high_or_quick(free_client):
    client, path = free_client
    result = _select_free(client)
    assert result["profile"] == "free"
    assert result["use_profile_models"] is True
    assert result["models"] == list(FREE_MODELS)
    assert result["chairman"] == FREE_CHAIRMAN
    assert result["resolved_chairman"] == FREE_CHAIRMAN
    assert result["options"]["profiles"]["free"]["models"] == list(FREE_MODELS)

    import yaml
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["tiers"]["default"] == "high"  # The original native tier is preserved
    assert saved["council"]["models"] == list(FREE_MODELS)
    assert saved["council"]["chairman"] == FREE_CHAIRMAN
    assert saved["council"]["style_normalization"] is False
    assert saved["gateways"]["default"] == "openrouter"
    assert saved["gateways"]["fallback"]["enabled"] is False
    assert get_config().council.models == list(FREE_MODELS)
    # Independent Quick pool remains exactly Amiable's default definition.
    from llm_council.default_pools import default_pool_models
    assert get_config().tiers.pools["quick"].models == default_pool_models()["quick"]


@pytest.mark.parametrize("setting", [
    {"use_profile_models": False},
    {"chairman": "openai/gpt-4o"},
    {"style_normalization": "auto"},
    {"rubric_enabled": True},
])
def test_free_rejects_paid_or_uncontrolled_settings(free_client, setting):
    client, _path = free_client
    payload = {"profile": "free", "style_normalization": "off", **setting}
    r = client.put("/api/settings", json=payload)
    assert r.status_code == 422


def test_free_requires_key_and_does_not_start_models(free_client, monkeypatch):
    client, _path = free_client
    _select_free(client)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("No paid council should be started before key setup")
    monkeypatch.setattr(main, "run_full_council", forbidden)
    conv = client.post("/api/conversations", json={}).json()
    r = client.post(f"/api/conversations/{conv['id']}/message", json={"content": "Hello"})
    assert r.status_code == 400
    assert "OpenRouter API key" in r.json()["detail"]
    stream = client.post(
        f"/api/conversations/{conv['id']}/message/stream", json={"content": "Hello"}
    )
    assert stream.status_code == 400
    assert not storage.get_conversation(conv["id"])["messages"]


def test_free_first_round_avoids_paid_title_and_uses_native_engine(free_client, monkeypatch):
    client, _ = free_client
    _select_free(client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-free-test")
    queries = []
    async def fake_council(question, **kwargs):
        queries.append((question, kwargs, list(get_config().council.models),
                        get_config().council.chairman))
        return _answer()
    async def forbidden_title(_question):
        raise AssertionError("Paid Gemini title must never be called in Free")
    monkeypatch.setattr(main, "run_full_council", fake_council)
    monkeypatch.setattr(main, "generate_conversation_title", forbidden_title)
    conv = client.post("/api/conversations", json={}).json()
    r = client.post(f"/api/conversations/{conv['id']}/message", json={
        "content": "Does the new Council work?", "verdict_type": "binary",
    })
    assert r.status_code == 200, r.text
    assert len(queries) == 1
    assert queries[0][0] == "Does the new Council work?"
    assert queries[0][2] == list(FREE_MODELS)
    assert queries[0][3] == FREE_CHAIRMAN
    assert storage.get_conversation(conv["id"])["title"].startswith("Does the new")


def test_free_followup_summarization_never_uses_paid_flash(free_client, monkeypatch):
    client, _path = free_client
    _select_free(client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-free-test")
    council_calls, summaries = [], []
    async def fake_council(question, **kwargs):
        council_calls.append(question)
        return _answer("A" * 7000 if len(council_calls) == 1 else "Second answer")
    async def fake_summary(model, messages, **kwargs):
        summaries.append((model, json.loads(messages[1]["content"]), kwargs))
        return {"content": "Free memory: the Council previously agreed on a plan."}
    async def forbidden_title(_question):
        raise AssertionError("Title would use a paid model")
    monkeypatch.setattr(main, "run_full_council", fake_council)
    monkeypatch.setattr(main, "generate_conversation_title", forbidden_title)
    monkeypatch.setattr(conversation_context, "query_model", fake_summary)
    conv = client.post("/api/conversations", json={}).json()
    endpoint = f"/api/conversations/{conv['id']}/message/stream"
    for question in ("First question", "Follow up?"):
        r = client.post(endpoint, json={"content": question})
        assert r.status_code == 200
        events = [json.loads(line[6:]) for line in r.text.splitlines()
                  if line.startswith("data: ")]
        assert any(e["type"] == "complete" for e in events)
    assert summaries[0][0] == FREE_SUMMARY_MODEL
    assert summaries[0][0].endswith(":free")
    assert summaries[0][2]["disable_tools"] is True
    assert council_calls[1].endswith("Follow up?")
    assert "Free memory:" in council_calls[1]
    assert storage.get_conversation(conv["id"])["context_memory"]["model"] == FREE_SUMMARY_MODEL


def test_switching_out_of_free_keeps_native_profiles_compatible(free_client):
    client, _path = free_client
    _select_free(client)
    result = client.put("/api/settings", json={
        "profile": "quick", "use_profile_models": True,
    })
    assert result.status_code == 200, result.text
    assert result.json()["profile"] == "quick"
    assert result.json()["resolved_chairman"] != FREE_CHAIRMAN
    assert result.json()["models"] != list(FREE_MODELS)


def test_free_fails_closed_on_environment_model_override(free_client, monkeypatch):
    client, _path = free_client
    _select_free(client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-free-test")
    monkeypatch.setenv("LLM_COUNCIL_CHAIRMAN", "openai/gpt-4o")
    reload_config()
    conv = client.post("/api/conversations", json={}).json()
    r = client.post(f"/api/conversations/{conv['id']}/message", json={"content": "Hello"})
    assert r.status_code == 409
    assert storage.get_conversation(conv["id"])["messages"] == []
    monkeypatch.delenv("LLM_COUNCIL_CHAIRMAN", raising=False)


@pytest.mark.parametrize("native_tier", ["quick", "balanced", "high", "reasoning"])
def test_free_is_overlay_not_alias_of_native_tier(free_client, native_tier):
    client, path = free_client
    from llm_council.default_pools import default_pool_models
    import yaml

    native = default_pool_models()[native_tier]
    initial = client.put("/api/settings", json={
        "profile": native_tier,
        "use_profile_models": True,
    })
    assert initial.status_code == 200, initial.text
    assert initial.json()["models"] == native

    free = _select_free(client)
    assert free["profile"] == "free"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert config["tiers"]["default"] == native_tier
    assert get_config().tiers.default == native_tier
    assert free["models"] == list(FREE_MODELS)
    assert get_config().tiers.pools["quick"].models == default_pool_models()["quick"]

    restored = client.put("/api/settings", json={
        "profile": native_tier,
        "use_profile_models": True,
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()["profile"] == native_tier
    assert restored.json()["models"] == native
    assert restored.json()["chairman"] is None
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["tiers"]["default"] == native_tier


def test_legacy_free_config_using_quick_is_still_recognized(free_client):
    client, path = free_client
    from backend.free_profile import is_free_document
    import yaml

    _select_free(client)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["tiers"]["default"] = "quick"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    reload_config()
    assert is_free_document(document)
    assert client.get("/api/settings").json()["profile"] == "free"
    # Saving Free should not rewrite even legacy native tiers.
    _select_free(client)
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["tiers"]["default"] == "quick"


def test_new_install_has_free_models_without_assigning_quick():
    from pathlib import Path
    import yaml
    path = Path(__file__).resolve().parents[1] / "llm_council.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["tiers"]["default"] == "high"
    assert raw["council"]["models"] == list(FREE_MODELS)
    assert raw["council"]["chairman"] == FREE_CHAIRMAN


def test_free_stage3_rotates_only_free_chairmen_on_rate_limit(free_client, monkeypatch):
    client, _ = free_client
    _select_free(client)
    from backend import engine_runtime
    import llm_council.council as council

    monkeypatch.setattr(engine_runtime, "FREE_CHAIRMAN_RETRY_PAUSE_SECONDS", 0)
    calls = []

    async def native_stage3(*args, **kwargs):
        model = council._get_chairman_model()
        calls.append((model, args, kwargs))
        if model == FREE_CHAIRMAN:
            return ({"model": model, "response": "429", "error_status": "rate_limited"},
                    {"total_tokens": 0}, None)
        return ({"model": model, "response": "Recovered final synthesis"},
                {"total_tokens": 35}, None)

    wrapped = engine_runtime.wrap_stage("stage3", native_stage3)
    token = engine_runtime.watch_free_chairman_retry(True)
    try:
        result, usage, verdict = asyncio.run(wrapped("question", verdict_type="synthesis"))
    finally:
        engine_runtime.unwatch_free_chairman_retry(token)

    assert [x[0] for x in calls] == [FREE_CHAIRMAN, FREE_MODELS[1]]
    assert all(model.endswith(":free") for model, *_ in calls)
    assert calls[0][1:] == calls[1][1:]  # same native stage 3 inputs
    assert result["model"] == FREE_MODELS[1]
    assert result["response"] == "Recovered final synthesis"
    assert usage["total_tokens"] == 35
    assert council._get_chairman_model() == FREE_CHAIRMAN  # no leaked override


def test_all_free_chairmen_rate_limited_returns_explicit_failure(free_client, monkeypatch):
    client, _ = free_client
    _select_free(client)
    from backend import engine_runtime
    import llm_council.council as council

    monkeypatch.setattr(engine_runtime, "FREE_CHAIRMAN_RETRY_PAUSE_SECONDS", 0)
    calls = []
    progress = []

    async def native_stage3(*args, **kwargs):
        model = council._get_chairman_model()
        calls.append(model)
        return ({"model": model, "response": "429",
                 "error_status": "rate_limited"},
                {"total_tokens": 0}, None)

    async def capture(data):
        progress.append(data)

    wrapped = engine_runtime.wrap_stage("stage3", native_stage3)

    async def drive():
        retry_token = engine_runtime.watch_free_chairman_retry(True)
        progress_token = engine_runtime.watch_progress(capture)
        try:
            return await wrapped("question")
        finally:
            engine_runtime.unwatch_progress(progress_token)
            engine_runtime.unwatch_free_chairman_retry(retry_token)

    result, usage, verdict = asyncio.run(drive())
    assert calls == list(FREE_MODELS)
    assert result["error_status"] == "rate_limited"
    assert result["free_fallback_exhausted"] is True
    assert "Please wait" in result["response"]
    assert len([e for e in progress if e["state"] == "retrying"]) == 2
    assert progress[-1]["state"] == "failed"
    assert council._get_chairman_model() == FREE_CHAIRMAN


@pytest.mark.parametrize("status", ["auth_error", "error", "timeout"])
def test_non_rate_limit_free_failure_never_rotates(free_client, monkeypatch, status):
    client, _ = free_client
    _select_free(client)
    from backend import engine_runtime
    import llm_council.council as council

    calls = []
    async def native_stage3(*args, **kwargs):
        calls.append(council._get_chairman_model())
        return ({"model": calls[-1], "response": "failure",
                 "error_status": status},
                {"total_tokens": 0}, None)

    retry_token = engine_runtime.watch_free_chairman_retry(True)
    try:
        result, _, _ = asyncio.run(engine_runtime.wrap_stage("stage3", native_stage3)())
    finally:
        engine_runtime.unwatch_free_chairman_retry(retry_token)
    assert calls == [FREE_CHAIRMAN]
    assert result["error_status"] == status


def test_paid_profiles_never_use_free_chairman_recovery(free_client, monkeypatch):
    client, _ = free_client
    _select_free(client)
    from backend import engine_runtime
    import llm_council.council as council
    calls = []

    async def native_stage3(*args, **kwargs):
        calls.append(council._get_chairman_model())
        return ({"model": calls[-1], "response": "429",
                 "error_status": "rate_limited"},
                {"total_tokens": 0}, None)

    result, _, _ = asyncio.run(
        engine_runtime.wrap_stage("stage3", native_stage3)("question")
    )
    assert calls == [FREE_CHAIRMAN]
    assert result["error_status"] == "rate_limited"


def test_free_retry_context_does_not_leak_to_concurrent_sessions(free_client, monkeypatch):
    client, _ = free_client
    _select_free(client)
    from backend import engine_runtime
    import llm_council.council as council

    monkeypatch.setattr(engine_runtime, "FREE_CHAIRMAN_RETRY_PAUSE_SECONDS", 0)
    calls = []

    async def native_stage3(name):
        model = council._get_chairman_model()
        calls.append((name, model))
        await asyncio.sleep(0)
        if name == "free" and model == FREE_CHAIRMAN:
            return ({"model": model, "response": "429",
                     "error_status": "rate_limited"}, {}, None)
        return ({"model": model, "response": "OK"}, {}, None)

    wrapped = engine_runtime.wrap_stage("stage3", native_stage3)

    async def free_run():
        token = engine_runtime.watch_free_chairman_retry(True)
        try:
            return await wrapped("free")
        finally:
            engine_runtime.unwatch_free_chairman_retry(token)

    async def both():
        return await asyncio.gather(free_run(), wrapped("other"))

    results = asyncio.run(both())
    assert results[0][0]["model"] == FREE_MODELS[1]
    assert results[1][0]["model"] == FREE_CHAIRMAN
    assert ("other", FREE_CHAIRMAN) in calls
    assert all(model in FREE_MODELS for _, model in calls)


@pytest.mark.parametrize("stream", [False, True])
def test_free_profile_sets_retry_context_for_both_api_paths(free_client, monkeypatch, stream):
    client, _ = free_client
    _select_free(client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-free-test")
    from backend import engine_runtime

    observed = []
    async def fake_native(question, **kwargs):
        observed.append(engine_runtime._free_chairman_retry.get())
        return _answer()

    monkeypatch.setattr(main, "run_full_council", fake_native)
    conv = client.post("/api/conversations", json={}).json()
    suffix = "/message/stream" if stream else "/message"
    r = client.post(f"/api/conversations/{conv['id']}{suffix}",
                    json={"content": "Does free fallback work?"})
    assert r.status_code == 200, r.text
    if stream:
        assert any('"type": "complete"' in line for line in r.text.splitlines())
    assert observed == [True]
    assert engine_runtime._free_chairman_retry.get() is False



def test_free_chairman_429_then_404_then_third_free_model_succeeds(
    free_client, monkeypatch
):
    client, _ = free_client
    _select_free(client)
    from backend import engine_runtime
    import llm_council.council as council

    monkeypatch.setattr(engine_runtime, "FREE_CHAIRMAN_RETRY_PAUSE_SECONDS", 0)
    calls = []
    progress = []

    async def native_stage3(*args, **kwargs):
        model = council._get_chairman_model()
        calls.append((model, args, kwargs))
        if model == FREE_MODELS[0]:
            return ({"model": model, "response": "rate-limited",
                     "error_status": "rate_limited",
                     "error_detail": "Rate limited by primary"}, {}, None)
        if model == FREE_MODELS[1]:
            return ({"model": model, "response": "HTTP 404",
                     "error_status": "error",
                     "error_detail": (
                         "HTTPStatusError: Client error '404 Not Found' for url "
                         "'https://openrouter.ai/api/v1/chat/completions'"
                     )}, {}, None)
        return ({"model": model, "response": "Chairman succeeded"},
                {"total_tokens": 92}, None)

    async def capture(event):
        progress.append(event)

    async def drive():
        token = engine_runtime.watch_free_chairman_retry(True)
        progress_token = engine_runtime.watch_progress(capture)
        try:
            return await engine_runtime.wrap_stage("stage3", native_stage3)(
                "question", verdict_type="synthesis"
            )
        finally:
            engine_runtime.unwatch_progress(progress_token)
            engine_runtime.unwatch_free_chairman_retry(token)

    final, usage, verdict = asyncio.run(drive())
    assert [entry[0] for entry in calls] == list(FREE_MODELS)
    assert all(entry[1:] == calls[0][1:] for entry in calls)
    assert final == {"model": FREE_MODELS[2], "response": "Chairman succeeded"}
    assert usage["total_tokens"] == 92
    assert [e["model"] for e in progress if e["state"] == "retrying"] == list(FREE_MODELS[1:])
    assert progress[-1]["state"] == "completed"
    assert council._get_chairman_model() == FREE_CHAIRMAN


@pytest.mark.parametrize("detail", [
    "HTTPStatusError: Server error '500 Internal Server Error' for url",
    "HTTPStatusError: Server error '502 Bad Gateway' for url",
    "HTTPStatusError: Server error '503 Service Unavailable' for url",
    "HTTPStatusError: Server error '504 Gateway Timeout' for url",
])
def test_free_chairman_provider_errors_recover_with_second_free_model(
    free_client, monkeypatch, detail
):
    client, _ = free_client
    _select_free(client)
    from backend import engine_runtime
    import llm_council.council as council
    monkeypatch.setattr(engine_runtime, "FREE_CHAIRMAN_RETRY_PAUSE_SECONDS", 0)
    calls = []

    async def stage3(*args, **kwargs):
        model = council._get_chairman_model()
        calls.append(model)
        if len(calls) == 1:
            return ({"model": model, "error_status": "error",
                     "error_detail": detail, "response": "failed"}, {}, None)
        return ({"model": model, "response": "Succeeded"}, {}, None)

    token = engine_runtime.watch_free_chairman_retry(True)
    try:
        final, _, _ = asyncio.run(engine_runtime.wrap_stage("stage3", stage3)())
    finally:
        engine_runtime.unwatch_free_chairman_retry(token)
    assert calls == list(FREE_MODELS[:2])
    assert final["response"] == "Succeeded"


@pytest.mark.parametrize("detail", [
    "HTTPStatusError: Client error '401 Unauthorized' for url",
    "HTTPStatusError: Client error '402 Payment Required' for url",
    "HTTPStatusError: Client error '400 Bad Request' for url",
    "Something went wrong before a provider was selected",
])
def test_free_chairman_non_retryable_errors_stop_immediately(
    free_client, monkeypatch, detail
):
    client, _ = free_client
    _select_free(client)
    from backend import engine_runtime
    import llm_council.council as council
    calls = []

    async def stage3(*args, **kwargs):
        calls.append(council._get_chairman_model())
        return ({"model": calls[-1], "error_status": "error",
                 "error_detail": detail, "response": "failed"}, {}, None)

    token = engine_runtime.watch_free_chairman_retry(True)
    try:
        final, _, _ = asyncio.run(engine_runtime.wrap_stage("stage3", stage3)())
    finally:
        engine_runtime.unwatch_free_chairman_retry(token)
    assert calls == [FREE_CHAIRMAN]
    assert final["error_detail"] == detail


def test_free_chairman_429_and_404_and_503_all_fail_fairly(
    free_client, monkeypatch
):
    client, _ = free_client
    _select_free(client)
    from backend import engine_runtime
    import llm_council.council as council
    monkeypatch.setattr(engine_runtime, "FREE_CHAIRMAN_RETRY_PAUSE_SECONDS", 0)
    calls = []
    statuses = [
        ("rate_limited", "Rate limited"),
        ("error", "HTTPStatusError: 404 Not Found"),
        ("error", "HTTPStatusError: 503 Service Unavailable"),
    ]

    async def stage3(*args, **kwargs):
        model = council._get_chairman_model()
        calls.append(model)
        status, detail = statuses[len(calls) - 1]
        return ({"model": model, "error_status": status,
                 "error_detail": detail, "response": "failed"}, {}, None)

    token = engine_runtime.watch_free_chairman_retry(True)
    try:
        final, _, _ = asyncio.run(engine_runtime.wrap_stage("stage3", stage3)())
    finally:
        engine_runtime.unwatch_free_chairman_retry(token)
    assert calls == list(FREE_MODELS)
    assert final["free_fallback_exhausted"] is True
    assert final["error_status"] == "error"
    assert "Please wait" in final["response"]
    assert [x["status"] for x in final["free_chairman_attempts"]] == [
        "rate_limited", "error", "error"
    ]


def test_legacy_stage3_failure_text_shown_as_error():
    from pathlib import Path
    component = Path(__file__).resolve().parents[1] / "frontend" / "src" / "components" / "Stage3.jsx"
    source = component.read_text(encoding="utf-8")
    assert "Error: Unable to generate final synthesis" in source
    assert "stage3-failed" in source


def test_free_pool_matches_three_actual_http_200_probe_candidates():
    """2026-10-28 account-probe evidence, not a guarantee of later uptime."""
    assert FREE_MODELS == (
        "nvidia/nemotron-3.5-super-vl:free",
        "dots-studio/dots-3-note-preview:free",
        "cohere/north-mini-code:free",
    )
    assert FREE_CHAIRMAN == FREE_MODELS[0]
    assert FREE_SUMMARY_MODEL in FREE_MODELS
    assert not set(FREE_MODELS).intersection(LEGACY_FREE_MODELS)


def test_saved_beta5_free_pool_is_recognized_but_blocked_until_upgrade(free_client, monkeypatch):
    import yaml
    client, path = free_client
    _select_free(client)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    native_tier = document["tiers"]["default"]
    document["council"]["models"] = list(LEGACY_FREE_MODELS)
    document["council"]["chairman"] = LEGACY_FREE_CHAIRMAN
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    reload_config()
    assert is_legacy_free_document(document)
    assert is_free_document(document)

    settings = client.get("/api/settings")
    assert settings.status_code == 200
    payload = settings.json()
    assert payload["profile"] == "free"
    assert payload["use_profile_models"] is True
    assert payload["free_profile_upgrade_required"] is True
    assert payload["options"]["profiles"]["free"]["models"] == list(FREE_MODELS)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-free-test")
    async def no_calls(*_args, **_kwargs):
        raise AssertionError("Legacy Free must never send requests as paid")
    monkeypatch.setattr(main, "run_full_council", no_calls)
    conv = client.post("/api/conversations", json={}).json()
    url = f"/api/conversations/{conv['id']}/message"
    blocked = client.post(url, json={"content": "Hello"})
    assert blocked.status_code == 409
    assert "Save settings" in blocked.json()["detail"]
    assert storage.get_conversation(conv["id"])["messages"] == []

    # Saving the existing Free selection with its formerly saved Chairman must
    # upgrade just the Free pool; no secret, transcript, native tier, or gateway
    # fallback into paid models is touched.
    saved = client.put("/api/settings", json={
        "profile": "free",
        "use_profile_models": True,
        "chairman": LEGACY_FREE_CHAIRMAN,
        "style_normalization": "off",
        "rubric_enabled": False,
        "safety_enabled": False,
        "bias_audit_enabled": False,
    })
    assert saved.status_code == 200, saved.text
    result = saved.json()
    assert result["profile"] == "free"
    assert result["free_profile_upgrade_required"] is False
    assert result["models"] == list(FREE_MODELS)
    assert result["chairman"] == FREE_CHAIRMAN
    assert result["api_key_configured"] is True
    new_doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert new_doc["tiers"]["default"] == native_tier
    assert new_doc["council"]["models"] == list(FREE_MODELS)
    assert new_doc["council"]["chairman"] == FREE_CHAIRMAN
    assert new_doc["gateways"]["fallback"]["enabled"] is False
    assert storage.get_conversation(conv["id"])["messages"] == []


@pytest.mark.parametrize("native_tier", ["quick", "balanced", "high", "reasoning"])
def test_legacy_free_upgrade_never_overwrites_native_tiers(free_client, native_tier):
    import yaml
    client, path = free_client
    before = client.put("/api/settings", json={
        "profile": native_tier, "use_profile_models": True,
    })
    assert before.status_code == 200, before.text
    from llm_council.default_pools import default_pool_models
    native_models = default_pool_models()[native_tier]

    _select_free(client)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["council"]["models"] = list(LEGACY_FREE_MODELS)
    document["council"]["chairman"] = LEGACY_FREE_CHAIRMAN
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    reload_config()

    upgraded = _select_free(client)
    assert upgraded["profile"] == "free"
    assert get_config().tiers.default == native_tier
    assert get_config().tiers.pools[native_tier].models == native_models

    leave = client.put("/api/settings", json={
        "profile": native_tier, "use_profile_models": True,
    })
    assert leave.status_code == 200
    assert leave.json()["models"] == native_models
    assert leave.json()["chairman"] is None


def test_unrelated_custom_paid_config_not_misclassified_as_free(free_client):
    import yaml
    client, path = free_client
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    doc["council"] = {
        "models": [*LEGACY_FREE_MODELS[:2], "openai/gpt-4o"],
        "chairman": LEGACY_FREE_CHAIRMAN,
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    reload_config()
    assert is_legacy_free_document(doc) is False
    assert is_free_document(doc) is False
    result = client.get("/api/settings").json()
    assert result["profile"] != "free"
    assert result["free_profile_upgrade_required"] is False
