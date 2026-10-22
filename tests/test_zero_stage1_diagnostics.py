"""Regression coverage for native Amiable zero-response Stage 1 failures.

These are deterministic offline tests; they never call OpenRouter.
"""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import engine_runtime, main, storage


@pytest.mark.parametrize("stream", [False, True])
def test_zero_stage1_is_not_persisted_as_success(tmp_path, monkeypatch, stream):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))

    async def failed_native(_query, **_kwargs):
        # This is the exact 0.53.0 return shape when all models fail.
        await engine_runtime.emit_progress("stage1", "started")
        await engine_runtime.emit_progress("stage1", "failed", responses=0)
        return [], [], {
            "model": "error", "response": "All models failed to respond. Please try again."
        }, {"usage": {"total": {"total_tokens": 0}}}

    async def no_title(_question):
        return "No models responded"

    monkeypatch.setattr(main, "run_full_council", failed_native)
    monkeypatch.setattr(main, "generate_conversation_title", no_title)
    client = TestClient(main.app)
    conv = client.post("/api/conversations", json={}).json()
    uri = f"/api/conversations/{conv['id']}/message"
    if stream:
        uri += "/stream"
    response = client.post(uri, json={"content": "Help me decide"})
    assert response.status_code == 200, response.text

    if stream:
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: ")]
        payload = next(e["data"] for e in events if e["type"] == "stage3_complete")
        assert next(e["metadata"] for e in events if e["type"] == "complete")["status"] == "failed"
        assert not any(e["type"] == "engine_progress" and
                       e["data"].get("stage") == "stage3" for e in events)
    else:
        payload = response.json()["stage3"]
        assert response.json()["metadata"]["status"] == "failed"

    assert payload["model"] == "error"
    assert payload["error_status"] == "no_responses"
    assert "No Council models returned" in payload["response"]
    assert payload["model_failures"] == []
    stored = storage.get_conversation(conv["id"])["messages"][-1]
    assert stored["stage3"] == payload
    assert stored["metadata"]["status"] == "failed"


def test_stage1_capture_records_only_safe_status_not_secrets():
    models = [
        "google/gemma-4-31b-it:free",
        "qwen/qwen3.8-27b:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
    ]
    results = [
        {"status": "rate_limited", "error": "Rate limited by model",
         "retry_after": 60},
        {"status": "error", "error": (
            "HTTPStatusError: Client error '404 Not Found' for url /v1/chat/completions "
            "secret-key-placeholder"
        )},
        {"status": "auth_error", "error": "Authentication failed"},
    ]
    calls = []

    async def native_status(model, *_args, **_kwargs):
        calls.append(model)
        return results[models.index(model)]

    wrapped_status = engine_runtime.wrap_model_status(native_status)

    async def native_stage1():
        await asyncio.gather(*(wrapped_status(model, [{"role": "user", "content": "q"}])
                               for model in models))
        return [], {"total_tokens": 0}

    async def drive():
        token = engine_runtime.watch_model_diagnostics()
        try:
            wrapped = engine_runtime.wrap_stage("stage1", native_stage1)
            await wrapped()
            return engine_runtime.get_model_diagnostics()
        finally:
            engine_runtime.unwatch_model_diagnostics(token)

    recorded = asyncio.run(drive())
    assert calls == models
    assert [item["status"] for item in recorded] == [
        "rate_limited", "error", "auth_error"
    ]
    assert recorded[0]["retry_after_seconds"] == 60
    assert recorded[1]["http_status"] == 404
    assert "secret-key" not in str(recorded)
    assert engine_runtime.get_model_diagnostics() == []


def test_model_capture_is_scoped_to_stage1_and_to_each_request():
    async def native_status(_model, *_args, **_kwargs):
        return {"status": "rate_limited", "error": "Rate limited"}

    wrapped_status = engine_runtime.wrap_model_status(native_status)

    async def native_stage1():
        await wrapped_status("free/model", [])
        return [], {}

    async def drive():
        token = engine_runtime.watch_model_diagnostics()
        try:
            await engine_runtime.wrap_stage("stage1", native_stage1)()
            await wrapped_status("chairman/model", [])  # Not a Stage 1 call
            return engine_runtime.get_model_diagnostics()
        finally:
            engine_runtime.unwatch_model_diagnostics(token)

    assert asyncio.run(drive()) == [
        {"model": "free/model", "status": "rate_limited"}
    ]
    assert engine_runtime.get_model_diagnostics() == []


def test_stage1_failure_messages_do_not_overstate_quota_causes():
    func = engine_runtime.no_model_response_message
    assert "may have been reached" in func([
        {"model": "one", "status": "rate_limited"}
    ], True)
    assert "API key" in func([{"status": "auth_error"}], True)
    assert "HTTP 404" in func([{"status": "error", "http_status": 404}], True)
    assert "Check your OpenRouter key" in func([], True)
    assert "Free endpoints" not in func([], False)


def test_old_success_colored_zero_response_now_detected():
    source = (Path(__file__).resolve().parents[1] / "frontend" / "src" /
              "components" / "Stage3.jsx").read_text(encoding="utf-8")
    assert "noResponses" in source
    assert "no_responses" in source
    assert "all models failed to respond" in source
    assert "model_failures" in source
    assert "stage3-failed" in source
