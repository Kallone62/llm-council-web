"""Regression checks for Stage 3's empty-result boundary, not native execution."""
import asyncio
import copy

import pytest

from backend import engine_runtime


INVALID_RESPONSES = [
    {"response": ""},
    {"response": " \n\t"},
    {"response": None},
    {},
    {"response": 42},
]


def _drive_stage3(response, free_mode, monkeypatch):
    events = []
    calls = []
    usage = {"total_tokens": 17}
    verdict = object()

    async def original(*args, **kwargs):
        calls.append((args, kwargs))
        return response, usage, verdict

    async def capture(event):
        events.append(event)

    monkeypatch.setattr(engine_runtime.council, "_get_chairman_model", lambda: engine_runtime.FREE_MODELS[0])

    async def run():
        progress_token = engine_runtime.watch_progress(capture)
        free_token = engine_runtime.watch_free_chairman_retry(free_mode)
        try:
            return await engine_runtime.wrap_stage("stage3", original)("question")
        finally:
            engine_runtime.unwatch_free_chairman_retry(free_token)
            engine_runtime.unwatch_progress(progress_token)

    result = asyncio.run(run())
    assert calls == [(('question',), {})]
    assert result[1] is usage
    assert result[2] is verdict
    return result[0], events


@pytest.mark.parametrize("fields", INVALID_RESPONSES)
@pytest.mark.parametrize("free_mode", [False, True])
def test_empty_chairman_stage_is_failed(fields, free_mode, monkeypatch):
    response = {"model": "provider/chairman", **fields}
    original = copy.deepcopy(response)
    final, events = _drive_stage3(response, free_mode, monkeypatch)
    assert final["error_status"] == "empty_synthesis"
    assert final["model"] == response["model"]
    assert isinstance(final["response"], str) and final["response"].strip()
    assert events == [
        {"stage": "stage3", "state": "started"},
        {"stage": "stage3", "state": "failed", "error_status": "empty_synthesis"},
    ]
    assert response == original


def test_valid_chairman_stage_is_unchanged(monkeypatch):
    response = {"model": "provider/chairman", "response": "  Valid answer  "}
    final, events = _drive_stage3(response, False, monkeypatch)
    assert final is response
    assert events == [
        {"stage": "stage3", "state": "started"},
        {"stage": "stage3", "state": "completed"},
    ]


def test_existing_provider_error_is_preserved(monkeypatch):
    response = {"model": "provider/chairman", "response": "", "error_status": "rate_limited"}
    final, events = _drive_stage3(response, False, monkeypatch)
    assert final is response
    assert events[-1] == {"stage": "stage3", "state": "failed", "error_status": "rate_limited"}


@pytest.mark.parametrize("fields", INVALID_RESPONSES)
def test_empty_chairman_has_no_quality_metrics(fields, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("A missing synthesis must not be scored")

    monkeypatch.setattr(engine_runtime, "calculate_quality_metrics", forbidden)
    stage1 = [{"model": "provider/a", "response": "A"}, {"model": "provider/b", "response": "B"}]
    metadata = {
        "quality_metrics": {"core": {"synthesis_attribution": {"grounded": True}}},
        "aggregate_rankings": [{"model": "provider/a", "average_position": 1.0},
                               {"model": "provider/b", "average_position": 2.0}],
        "usage": {"total": {"total_tokens": 17}},
    }
    original = copy.deepcopy(metadata)
    result = engine_runtime.repair_quality_metrics(stage1, [], fields, metadata)
    assert result == {**original, "quality_metrics": None}
    assert metadata == original


def test_empty_chairman_does_not_enable_disabled_quality_metrics():
    metadata = {"usage": {"total": {"total_tokens": 17}}}
    assert engine_runtime.repair_quality_metrics([], [], {"response": " "}, metadata) is metadata
