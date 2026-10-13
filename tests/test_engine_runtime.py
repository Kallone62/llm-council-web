"""Offline checks for native stage instrumentation and quality-metric repair."""
import asyncio

from backend import engine_runtime


def test_native_progress_reports_real_boundaries_and_preserves_result():
    seen = []

    async def callback(event):
        seen.append(event)

    async def native_stage():
        await asyncio.sleep(0)
        return ([{"model": "test-model", "response": "hello"}], {"tokens": 1})

    async def run():
        token = engine_runtime.watch_progress(callback)
        try:
            return await engine_runtime.wrap_stage("stage1", native_stage)()
        finally:
            engine_runtime.unwatch_progress(token)

    result = asyncio.run(run())
    assert result[0][0]["response"] == "hello"
    assert seen == [
        {"stage": "stage1", "state": "started"},
        {"stage": "stage1", "state": "completed", "responses": 1},
    ]


def test_progress_is_isolated_between_concurrent_runs():
    seen_a, seen_b = [], []

    async def stage():
        await asyncio.sleep(0)
        return ([], {})

    wrapped = engine_runtime.wrap_stage("stage1", stage)

    async def worker(collector):
        async def callback(event):
            collector.append(event)

        token = engine_runtime.watch_progress(callback)
        try:
            await wrapped()
        finally:
            engine_runtime.unwatch_progress(token)

    async def both():
        await asyncio.gather(worker(seen_a), worker(seen_b))

    asyncio.run(both())
    assert len(seen_a) == len(seen_b) == 2
    assert all(event["stage"] == "stage1" for event in seen_a + seen_b)


def test_quality_correction_uses_native_amiable_calculator():
    stage1 = [
        {"model": "provider/a", "response": "apple banana answer result"},
        {"model": "provider/b", "response": "apple banana decision result"},
    ]
    stage3 = {"model": "provider/answer", "response": "apple banana answer result"}
    metadata = {
        "quality_metrics": {
            "core": {
                "synthesis_attribution": {
                    "winner_alignment": 0.0,
                    "max_source_alignment": 0.0,
                    "hallucination_risk": 1.0,
                    "grounded": False,
                }
            }
        },
        "aggregate_rankings": [
            {"model": "provider/a", "average_position": 1.0},
            {"model": "provider/b", "average_position": 2.0},
        ],
    }
    corrected = engine_runtime.repair_quality_metrics(stage1, [], stage3, metadata)
    attribution = corrected["quality_metrics"]["core"]["synthesis_attribution"]
    assert attribution["max_source_alignment"] > 0
    assert attribution["hallucination_risk"] < 1
    assert corrected["aggregate_rankings"] == metadata["aggregate_rankings"]


def test_quality_correction_respects_disabled_metrics():
    metadata = {"usage": {"total": {"cost_usd": 0.25}}}
    assert engine_runtime.repair_quality_metrics([], [], {}, metadata) is metadata
