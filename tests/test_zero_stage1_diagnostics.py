"""Stage 1 must remain the original Amiable call path.

After the reported Free regression, never monkeypatch model-query methods to
observe them. These offline tests guarantee zero-response UI handling does not
alter the network-facing model execution.
"""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import engine_runtime, main, storage
from llm_council import openrouter


@pytest.mark.parametrize("stream", [False, True])
def test_zero_stage1_is_reported_without_fake_stage3_success(tmp_path, monkeypatch, stream):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))

    async def native(_query, **kwargs):
        return [], [], {
            "model": "error",
            "response": "All models failed to respond. Please try again.",
        }, {"usage": {"total": {"total_tokens": 0}}}

    async def title(_question):
        return "Unavailable"
    monkeypatch.setattr(main, "run_full_council", native)
    monkeypatch.setattr(main, "generate_conversation_title", title)

    client = TestClient(main.app)
    conversation = client.post("/api/conversations", json={}).json()
    uri = f"/api/conversations/{conversation['id']}/message"
    if stream:
        uri += "/stream"
    response = client.post(uri, json={"content": "Help me decide"})
    assert response.status_code == 200, response.text
    if stream:
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: ")]
        payload = next(e["data"] for e in events if e["type"] == "stage3_complete")
        meta = next(e["metadata"] for e in events if e["type"] == "complete")
    else:
        payload = response.json()["stage3"]
        meta = response.json()["metadata"]

    assert payload["model"] == "error"
    assert payload["error_status"] == "no_responses"
    assert "No Council models returned" in payload["response"]
    assert "model_failures" not in payload
    assert "ui_model_failures" not in meta
    assert meta["status"] == "failed"
    stored = storage.get_conversation(conversation["id"])["messages"][-1]
    assert stored["stage3"] == payload


def test_stage1_does_not_monkeypatch_native_model_requests():
    from llm_council.openrouter import query_model_with_status
    assert engine_runtime.install_stage_hooks() is None
    assert openrouter.query_model_with_status is query_model_with_status
    assert not hasattr(engine_runtime, "wrap_model_status")
    assert not hasattr(engine_runtime, "_model_diagnostics")
    assert not hasattr(engine_runtime, "watch_model_diagnostics")


@pytest.mark.parametrize("responses", [0, 2])
def test_stage1_wrapper_passes_through_exact_args_and_returns(responses):
    observed = []
    async def original(*args, **kwargs):
        observed.append((args, kwargs))
        return ([{"model": f"provider/{n}", "response": "ok"}
                 for n in range(responses)], {"total_tokens": 13})

    async def drive():
        progress = []
        async def capture(event):
            progress.append(event)
        token = engine_runtime.watch_progress(capture)
        try:
            result = await engine_runtime.wrap_stage("stage1", original)(
                "example prompt", timeout=25, disable_tools=True,
            )
        finally:
            engine_runtime.unwatch_progress(token)
        return result, progress

    result, progress = asyncio.run(drive())
    assert result[1]["total_tokens"] == 13
    assert len(result[0]) == responses
    assert observed == [(("example prompt",), {
        "timeout": 25, "disable_tools": True,
    })]
    assert progress[0] == {"stage": "stage1", "state": "started"}
    assert progress[-1] == {
        "stage": "stage1",
        "state": "failed" if responses == 0 else "completed",
        "responses": responses,
    }


def test_old_native_no_response_placeholder_still_has_error_styling():
    source = (Path(__file__).resolve().parents[1] / "frontend" / "src" /
              "components" / "Stage3.jsx").read_text(encoding="utf-8")
    assert "noResponses" in source
    assert "no_responses" in source
    assert "all models failed to respond" in source
    assert "stage3-failed" in source
