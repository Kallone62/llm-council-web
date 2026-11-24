"""Regression: unsuccessful Council turns must not enter later decision memory."""
import asyncio
import copy
import json

import pytest

from backend import conversation_context as context


def _turn(question, stage3):
    return [{"role": "user", "content": question}, {"role": "assistant", "stage3": stage3}]


@pytest.mark.parametrize("status", ["error", "rate_limited", "no_responses"])
def test_failed_chairman_text_is_not_a_completed_round(monkeypatch, status):
    async def forbidden(*args, **kwargs):
        pytest.fail("Short valid history must not call the summary model")

    monkeypatch.setattr(context, "query_model", forbidden)
    conversation = {"messages": (
        _turn("Failed question", {"response": "Provider failure, please retry", "error_status": status})
        + _turn("Valid question", {"response": "Valid final answer"})
    )}
    original = copy.deepcopy(conversation)
    query, memory = asyncio.run(context.prepare_council_query(conversation, "Follow up"))
    assert "Failed question" not in query
    assert "Provider failure" not in query
    assert "Valid question" in query and "Valid final answer" in query
    assert query.endswith("Follow up")
    assert memory is None
    assert conversation == original


def test_legacy_summary_with_matching_count_is_not_reused(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Short valid history must not call the summary model")

    monkeypatch.setattr(context, "query_model", forbidden)
    conversation = {
        "messages": (
            _turn("Failed question", {"response": "Failure text", "error_status": "error"})
            + _turn("Valid question", {"response": "Valid answer"})
        ),
        "context_memory": {"summary": "Contaminated legacy summary", "covered_turns": 1},
    }
    original = copy.deepcopy(conversation)
    query, memory = asyncio.run(context.prepare_council_query(conversation, "Next"))
    assert "Contaminated legacy summary" not in query
    assert "Failed question" not in query
    assert "Valid answer" in query
    assert memory is None
    assert conversation == original


def test_rebuilt_summary_uses_only_successful_rounds_and_can_be_reused(monkeypatch):
    calls = []

    async def summarize(model, messages, **kwargs):
        calls.append(json.loads(messages[1]["content"]))
        return {"content": "Confirmed valid decision"}

    monkeypatch.setattr(context, "query_model", summarize)
    conversation = {
        "messages": (
            _turn("Failed question", {"response": "Failure text", "error_status": "error"})
            + _turn("Valid question", {"response": "Valid answer " * 700})
        ),
        "context_memory": {"summary": "Contaminated legacy summary", "covered_turns": 1},
    }
    original = copy.deepcopy(conversation)
    query, memory = asyncio.run(context.prepare_council_query(conversation, "Next"))
    assert calls == [{"previous_memory": None, "completed_exchanges_since_memory": [
        {"user_question": "Valid question", "chairman_final_answer": "Valid answer " * 700}
    ]}]
    assert memory["covered_turns"] == 1
    assert "Confirmed valid decision" in query
    assert "Failure text" not in query
    assert conversation == original
    conversation["context_memory"] = memory
    query, update = asyncio.run(context.prepare_council_query(conversation, "Again"))
    assert len(calls) == 1
    assert update is None
    assert "Confirmed valid decision" in query
    assert query.endswith("Again")
