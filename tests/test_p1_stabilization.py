"""P1 regressions; provider calls are mocked, not live acceptance tests."""
import asyncio

import pytest

from backend import config, conversation_context as context, engine_runtime
from backend.free_profile import FREE_MODELS, FREE_CHAIRMAN, is_free_document
from backend.result_status import classify_round, final_error, normalize_final


def conversation(answer, **extra):
    return {"messages": [
        {"role": "user", "content": "Previous question"},
        {"role": "assistant", "stage3": {"response": answer, **extra}},
    ]}


@pytest.mark.parametrize("answer,extra", [
    ("Provider failed", {"error_status": "rate_limited"}),
    ("Error: Unable to generate final synthesis (404)", {}),
    ("All models failed to respond. Please try again.", {"model": "error"}),
    ("  ", {}),
])
def test_failed_rounds_excluded_from_memory(answer, extra):
    query, memory = asyncio.run(context.prepare_council_query(conversation(answer, **extra), "Next"))
    assert query == "Next"
    assert memory is None


def test_free_memory_uses_alternative_and_does_not_cache_degraded_excerpt(monkeypatch):
    calls = []
    async def fake(model, *_args, **_kwargs):
        calls.append(model)
        return {"content": "Recovered summary"} if len(calls) == 2 else None
    monkeypatch.setattr(context, "query_model", fake)
    source = conversation("x" * 7000)
    query, memory = asyncio.run(context.prepare_council_query(
        source, "Next", summary_model=FREE_MODELS[0], summary_alternatives=FREE_MODELS,
        allow_degraded_history=True,
    ))
    assert calls == list(FREE_MODELS[:2])
    assert memory["model"] == FREE_MODELS[1]
    assert "Recovered summary" in query

    async def unavailable(*_args, **_kwargs):
        return None
    monkeypatch.setattr(context, "query_model", unavailable)
    states = []
    async def progress(state):
        states.append(state)
    query, memory = asyncio.run(context.prepare_council_query(
        source, "Next", summary_model=FREE_MODELS[0], summary_alternatives=FREE_MODELS,
        allow_degraded_history=True, progress_callback=progress,
    ))
    assert states == ["started", "degraded"]
    assert "truncated recent history" in query and query.endswith("Next")
    assert memory is None


def test_legacy_cached_memory_is_rebuilt(monkeypatch):
    source = conversation("x" * 7000)
    source["context_memory"] = {"summary": "Legacy polluted memory", "covered_turns": 1}
    async def fake(*_args, **_kwargs):
        return {"content": "Rebuilt"}
    monkeypatch.setattr(context, "query_model", fake)
    query, memory = asyncio.run(context.prepare_council_query(source, "Next"))
    assert "Legacy polluted" not in query and "Rebuilt" in query
    assert memory["source_digest"]


def test_round_status_preserves_partial_answers_and_disables_failed_verdict():
    responses = [{"response": "answer"}] * 3
    final = {"response": "Useful result"}
    malformed = [{"ranking": "text", "parsed_ranking": {"ranking": []}}]
    assert classify_round(responses, malformed, final, {})["status"] == "partial"
    assert classify_round(responses[:1], [], final, {})["status"] == "partial"
    reviews = [{"parsed_ranking": {"ranking": ["Response A"]}}] * 3
    assert classify_round(responses, reviews, final, {})["status"] == "complete"
    failed = normalize_final({"response": "  "})
    assert final_error(failed) == "empty_synthesis"
    result = classify_round(responses, reviews, failed, {"verdict": {}, "quality_metrics": {}})
    assert result["status"] == "failed" and "verdict" not in result and result["quality_metrics"] is None


def test_progress_empty_reviews_and_synthesis_are_failed():
    events = []
    async def progress(event):
        events.append(event)
    async def reviews():
        return ([{"ranking": "invalid", "parsed_ranking": {"ranking": []}}], {}, {})
    async def synthesis():
        return ({"response": " "}, {}, {"verdict": "bogus"})
    async def run():
        token = engine_runtime.watch_progress(progress)
        try:
            await engine_runtime.wrap_stage("stage2", reviews)()
            result = await engine_runtime.wrap_stage("stage3", synthesis)()
            assert result[0]["error_status"] == "empty_synthesis" and result[2] is None
        finally:
            engine_runtime.unwatch_progress(token)
    asyncio.run(run())
    assert [(e["stage"], e["state"]) for e in events] == [
        ("stage2", "started"), ("stage2", "failed"),
        ("stage3", "started"), ("stage3", "failed"),
    ]


def test_stale_free_preset_migrates_without_resetting_native_tier(tmp_path, monkeypatch):
    path = tmp_path / "llm_council.yaml"
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(path))
    monkeypatch.setattr(config, "DEFAULT_COUNCIL_CONFIG_PATH", path)
    path.write_text("tiers:\n  default: reasoning\ncouncil:\n  models: [old/model:free]\n  chairman: old/model:free\n  synthesis_mode: debate\n", encoding="utf-8")
    assert is_free_document(config.read_council_config_document())
    config.validate_installation_config()
    doc = config.read_council_config_document()
    assert doc["tiers"]["default"] == "reasoning"
    assert doc["council"]["synthesis_mode"] == "debate"
    assert doc["council"]["models"] == list(FREE_MODELS)
    assert doc["council"]["chairman"] == FREE_CHAIRMAN
    assert doc["gateways"]["fallback"]["enabled"] is False


def test_invalid_yaml_update_does_not_replace_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "llm_council.yaml"
    path.write_text("tiers:\n  default: high\n", encoding="utf-8")
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(path))
    with pytest.raises(ValueError):
        config.write_council_config_document({"invalid_top_level": True})
    assert path.read_text(encoding="utf-8") == "tiers:\n  default: high\n"
