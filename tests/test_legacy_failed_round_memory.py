"""Legacy beta errors must not be reused as successful conversation memory."""
import asyncio
import copy
import json

import pytest

from backend import conversation_context as context


def turn(question, answer, model="provider/chairman"):
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "stage3": {"model": model, "response": answer}},
    ]


@pytest.mark.parametrize("model,answer", [
    ("error", "All models failed to respond. Please try again."),
    ("provider/chairman", "Error: Unable to generate final synthesis."),
    ("provider/chairman", "ERROR: UNABLE TO GENERATE FINAL SYNTHESIS. Retry later."),
])
def test_legacy_error_text_is_excluded_from_history(monkeypatch, model, answer):
    async def forbidden(*args, **kwargs):
        pytest.fail("Short valid history must not call summary")

    monkeypatch.setattr(context, "query_model", forbidden)
    conversation = {"messages": turn("Failed question", answer, model) + turn("Valid question", "Valid answer")}
    original = copy.deepcopy(conversation)
    query, memory = asyncio.run(context.prepare_council_query(conversation, "Next"))
    assert "Failed question" not in query and answer not in query
    assert "Valid question" in query and "Valid answer" in query
    assert query.endswith("Next") and memory is None
    assert conversation == original


@pytest.mark.parametrize("answer", [
    "A previous system reported Error: Unable to generate final synthesis; here is the fix.",
    "All models failed to respond in yesterday's experiment. The fix is to retry.",
])
def test_valid_discussion_of_errors_is_not_removed(monkeypatch, answer):
    async def forbidden(*args, **kwargs):
        pytest.fail("Short history must not call summary")

    monkeypatch.setattr(context, "query_model", forbidden)
    query, memory = asyncio.run(context.prepare_council_query({"messages": turn("Question", answer)}, "Next"))
    assert answer in query and memory is None


def test_version_two_summary_is_not_reused_even_with_matching_count(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Short clean history must not call summary")

    monkeypatch.setattr(context, "query_model", forbidden)
    conversation = {
        "messages": turn("Failed question", "All models failed to respond. Please try again.", "error") + turn("Valid question", "Valid answer"),
        "context_memory": {"version": 2, "summary": "Contaminated version two memory", "covered_turns": 1},
    }
    original = copy.deepcopy(conversation)
    query, memory = asyncio.run(context.prepare_council_query(conversation, "Next"))
    assert "Contaminated version two memory" not in query
    assert "Failed question" not in query and "Valid answer" in query
    assert memory is None and conversation == original


def test_version_two_summary_rebuilt_from_clean_long_history_and_reused(monkeypatch):
    calls = []

    async def summarize(model, messages, **kwargs):
        calls.append(json.loads(messages[1]["content"]))
        return {"content": "Clean memory"}

    monkeypatch.setattr(context, "query_model", summarize)
    valid_answer = "Valid answer " * 700
    conversation = {
        "messages": turn("Failed question", "Error: Unable to generate final synthesis.") + turn("Valid question", valid_answer),
        "context_memory": {"version": 2, "summary": "Contaminated version two memory", "covered_turns": 1},
    }
    original = copy.deepcopy(conversation)
    query, memory = asyncio.run(context.prepare_council_query(conversation, "Next"))
    assert calls == [{"previous_memory": None, "completed_exchanges_since_memory": [
        {"user_question": "Valid question", "chairman_final_answer": valid_answer},
    ]}]
    assert memory["version"] == 3 and memory["covered_turns"] == 1
    assert "Clean memory" in query and "Contaminated version two memory" not in query
    assert conversation == original
    conversation["context_memory"] = memory
    query, update = asyncio.run(context.prepare_council_query(conversation, "Again"))
    assert len(calls) == 1 and update is None
    assert "Clean memory" in query and query.endswith("Again")
