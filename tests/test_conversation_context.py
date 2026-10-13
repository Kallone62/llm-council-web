"""Offline tests for faithful rolling AI memory and its Amiable boundary."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend import main, storage, conversation_context as context


def _result(answer="Council recommends Python"):
    return (
        [{"model": "provider/a", "response": "Raw Stage 1 internal"}],
        [{"model": "provider/b", "ranking": "Raw Stage 2 internal"}],
        {"model": "provider/chairman", "response": answer},
        {"aggregate_rankings": [], "usage": {"total": {"total_tokens": 12}}},
    )


def _conversation(exchanges):
    messages = []
    for question, answer in exchanges:
        messages.append({"role": "user", "content": question})
        messages.append({
            "role": "assistant",
            "stage1": [{"response": "Private Stage 1 critique"}],
            "stage2": [{"ranking": "Private Stage 2 score"}],
            "stage3": {"response": answer},
        })
    return {"messages": messages}


def test_first_question_is_unchanged_and_no_extra_llm(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("No summary model should be called")
    monkeypatch.setattr(context, "query_model", forbidden)
    query, memory = asyncio.run(context.prepare_council_query({"messages": []}, "First question"))
    assert query == "First question"
    assert memory is None


def test_short_history_is_full_not_character_truncated(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Small history should be passed directly")
    monkeypatch.setattr(context, "query_model", forbidden)
    long_but_small = "Important ending: revert if safety checks fail."
    conversation = _conversation([("Consider rollout?", "A" * 2000 + long_but_small)])
    query, memory = asyncio.run(context.prepare_council_query(conversation, "Why?"))
    assert "A" * 2000 + long_but_small in query
    assert query.endswith("Why?")
    assert memory is None
    assert "Private Stage 1 critique" not in query
    assert "Private Stage 2 score" not in query


def test_unanswered_or_failed_round_does_not_become_decision():
    messages = [
        {"role": "user", "content": "Unanswered old question"},
        {"role": "assistant", "stage3": None},
        {"role": "user", "content": "Completed question"},
        {"role": "assistant", "stage3": {"response": "Actual final"}},
        {"role": "user", "content": "Another unanswered question"},
    ]
    query, _ = asyncio.run(context.prepare_council_query({"messages": messages}, "Next"))
    assert "Unanswered old question" not in query
    assert "Another unanswered question" not in query
    assert "Completed question" in query and "Actual final" in query


def test_large_history_is_summarized_by_ai_without_cutting_source(monkeypatch):
    calls = []
    progress = []
    original_answer = "A" * 6300 + "CRITICAL FINAL DECISION: DO NOT SHIP."

    async def fake_model(model, messages, **kwargs):
        calls.append((model, messages, kwargs))
        return {"content": "Do not ship; the final decision is to wait for safety review."}

    async def on_progress(state):
        progress.append(state)

    monkeypatch.setattr(context, "query_model", fake_model)
    conversation = _conversation([("Should we ship?", original_answer)])
    query, memory = asyncio.run(context.prepare_council_query(
        conversation, "And tomorrow?", progress_callback=on_progress
    ))

    assert len(calls) == 1
    assert calls[0][0] == context.SUMMARY_MODEL
    assert calls[0][2]["disable_tools"] is True
    sent = json.loads(calls[0][1][1]["content"])
    assert sent["completed_exchanges_since_memory"][0]["chairman_final_answer"] == original_answer
    assert "CRITICAL FINAL DECISION" in sent["completed_exchanges_since_memory"][0]["chairman_final_answer"]
    assert "Do not ship" in query
    assert "A" * 100 not in query
    assert query.endswith("And tomorrow?")
    assert memory["covered_turns"] == 1
    assert progress == ["started", "completed"]


def test_cached_ai_memory_reused_and_then_refreshed_incrementally(monkeypatch):
    calls = []
    async def fake_model(model, messages, **kwargs):
        payload = json.loads(messages[1]["content"])
        calls.append(payload)
        return {"content": "Summary round " + str(len(calls))}

    monkeypatch.setattr(context, "query_model", fake_model)
    conversation = _conversation([("Q1", "Long " * 1500)])
    _, memory = asyncio.run(context.prepare_council_query(conversation, "Q2"))
    assert len(calls) == 1
    conversation["context_memory"] = memory
    query, no_update = asyncio.run(context.prepare_council_query(conversation, "Q2 again"))
    assert no_update is None
    assert len(calls) == 1
    assert "Summary round 1" in query
    conversation["messages"].extend(_conversation([("Q2", "Another " * 1500)])["messages"])
    query, new_memory = asyncio.run(context.prepare_council_query(conversation, "Q3"))
    assert len(calls) == 2
    assert calls[1]["previous_memory"] == "Summary round 1"
    assert len(calls[1]["completed_exchanges_since_memory"]) == 1
    assert calls[1]["completed_exchanges_since_memory"][0]["user_question"] == "Q2"
    assert new_memory["covered_turns"] == 2
    assert "Summary round 2" in query


def test_summary_failure_refuses_to_silently_discard_history(monkeypatch):
    progress = []
    async def fake_model(*_args, **_kwargs):
        return None

    async def on_progress(state):
        progress.append(state)

    monkeypatch.setattr(context, "query_model", fake_model)
    conversation = _conversation([("A", "Z" * 6800)])
    with pytest.raises(context.ContextSummaryError):
        asyncio.run(context.prepare_council_query(
            conversation, "Next", progress_callback=on_progress
        ))
    assert progress == ["started", "failed"]


def test_api_followups_use_same_context_and_keep_original_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    council_queries = []
    summary_calls = []

    async def fake_council(query, **_kwargs):
        council_queries.append(query)
        return _result("Answer #" + str(len(council_queries)) + (" X" * 3600 if len(council_queries) == 1 else ""))

    async def fake_title(_query):
        return "AI memory test"

    async def fake_summary(_model, _messages, **_kwargs):
        summary_calls.append(True)
        return {"content": "Council previously concluded Python is preferable."}

    monkeypatch.setattr(main, "run_full_council", fake_council)
    monkeypatch.setattr(main, "generate_conversation_title", fake_title)
    monkeypatch.setattr(context, "query_model", fake_summary)
    client = TestClient(main.app)
    conv = client.post("/api/conversations", json={}).json()
    other = client.post("/api/conversations", json={}).json()

    r1 = client.post(f"/api/conversations/{conv['id']}/message", json={"content": "Python or JS?"})
    r2 = client.post(f"/api/conversations/{conv['id']}/message", json={"content": "What about AI?"})
    r3 = client.post(f"/api/conversations/{other['id']}/message", json={"content": "Unrelated"})
    assert [r1.status_code, r2.status_code, r3.status_code] == [200, 200, 200]
    assert council_queries[0] == "Python or JS?"
    assert summary_calls == [True]
    assert "Council previously concluded Python is preferable." in council_queries[1]
    assert council_queries[1].endswith("What about AI?")
    assert council_queries[2] == "Unrelated"
    saved = storage.get_conversation(conv["id"])
    assert [x["content"] for x in saved["messages"] if x["role"] == "user"] == [
        "Python or JS?", "What about AI?"
    ]
    assert saved["context_memory"]["covered_turns"] == 1
    assert saved["messages"][1]["stage3"]["response"].endswith(" X" * 3600)


def test_sse_followup_runs_ai_summary_before_council_and_reports_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    prompts = []
    async def fake_council(query, **_kwargs):
        prompts.append(query)
        return _result("R" * 7000 if len(prompts) == 1 else "final two")

    async def fake_title(_query):
        return "SSE history test"

    async def fake_summary(_model, _messages, **_kwargs):
        return {"content": "The Council favored a cautious launch."}

    monkeypatch.setattr(main, "run_full_council", fake_council)
    monkeypatch.setattr(main, "generate_conversation_title", fake_title)
    monkeypatch.setattr(context, "query_model", fake_summary)
    client = TestClient(main.app)
    conv = client.post("/api/conversations", json={}).json()
    endpoint = f"/api/conversations/{conv['id']}/message/stream"
    assert client.post(endpoint, json={"content": "Initial?"}).status_code == 200
    result = client.post(endpoint, json={"content": "Follow up?"})
    events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
    actual_progress = [e["data"] for e in events if e["type"] == "engine_progress"]
    assert [(x["stage"], x["state"]) for x in actual_progress] == [
        ("memory", "started"), ("memory", "completed")
    ]
    assert "The Council favored a cautious launch." in prompts[1]
    assert prompts[1].endswith("Follow up?")
    assert storage.get_conversation(conv["id"])["context_memory"]["covered_turns"] == 1
    assert storage.get_conversation(conv["id"])["messages"][2] == {
        "role": "user", "content": "Follow up?"
    }


def test_summary_failure_returns_error_without_running_expensive_council(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    original = storage.create_conversation("4f82eb89-f777-4a36-a8f6-8991512c764e")
    storage.add_user_message(original["id"], "Original")
    storage.add_assistant_message(original["id"], [], [], {"response": "T" * 7000})

    async def fail_summary(*_args, **_kwargs):
        return None

    async def forbidden_council(*_args, **_kwargs):
        raise AssertionError("Should not start Council if memory failed")

    monkeypatch.setattr(context, "query_model", fail_summary)
    monkeypatch.setattr(main, "run_full_council", forbidden_council)
    client = TestClient(main.app)
    endpoint = f"/api/conversations/{original['id']}/message"
    response = client.post(endpoint, json={"content": "New"})
    assert response.status_code == 503
    assert "memory" in response.json()["detail"].lower()
    stream = client.post(endpoint + "/stream", json={"content": "New"})
    assert stream.status_code == 200
    assert '"type": "error"' in stream.text
    assert len(storage.get_conversation(original["id"])["messages"]) == 2
