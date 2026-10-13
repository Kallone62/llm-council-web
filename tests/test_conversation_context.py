"""Offline regression tests for true follow-up conversations.

No OpenRouter calls. These tests pin the stateless Amiable prompt boundary and
ensure saved user messages remain separate from bounded engine context.
"""

from fastapi.testclient import TestClient

from backend import main, storage
from backend.conversation_context import (
    MAX_HISTORY_CHARS,
    MAX_HISTORY_TURNS,
    build_council_query,
)


def _council_result(answer: str):
    return (
        [{"model": "model/a", "response": "Raw Stage 1 internal details"}],
        [{"model": "model/b", "ranking": "Raw Stage 2 private review"}],
        {"model": "model/chairman", "response": answer},
        {"aggregate_rankings": [], "usage": {"total": {"cost_usd": 0}}},
    )


def test_first_question_is_identical_with_no_history():
    query = "Explain the trade-offs."
    assert build_council_query([], query) == query
    # Neither pending questions nor incomplete council attempts count as prior decisions.
    messages = [
        {"role": "user", "content": "A question that never got an answer"},
        {"role": "assistant", "stage3": None},
    ]
    assert build_council_query(messages, query) == query


def test_context_excludes_unanswered_questions_and_raw_council_stages():
    messages = [
        {"role": "user", "content": "First actual question"},
        {"role": "assistant", "stage1": [{"response": "private Stage 1"}],
         "stage2": [{"ranking": "private Stage 2"}],
         "stage3": {"response": "Completed chairman response"}},
        {"role": "user", "content": "This failed without a final answer"},
    ]
    query = build_council_query(messages, "Follow up")
    assert "First actual question" in query
    assert "Completed chairman response" in query
    assert "This failed without a final answer" not in query
    assert "private Stage 1" not in query
    assert "private Stage 2" not in query
    assert query.endswith("Follow up")


def test_context_is_bounded_to_recent_successful_turns():
    messages = []
    for index in range(7):
        messages.extend([
            {"role": "user", "content": f"question_{index} " + "Q" * 3000},
            {"role": "assistant", "stage3": {"response": f"answer_{index} " + "A" * 5000}},
        ])
    query = build_council_query(messages, "new latest")
    assert "question_6" in query and "answer_6" in query
    assert "question_0" not in query and "answer_0" not in query
    assert "Raw Stage" not in query
    assert query.endswith("new latest")
    # The context segment is budgeted independently of the new user question.
    assert len(query.split("Current user question for this Council round:", 1)[0]) <= MAX_HISTORY_CHARS + 150
    assert query.count("Previous user question:") <= MAX_HISTORY_TURNS


def test_rest_followup_uses_history_without_altering_saved_user_text(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    prompts = []
    title_requests = []

    async def fake_council(query, **kwargs):
        prompts.append((query, kwargs))
        return _council_result(f"Final answer #{len(prompts)}")

    async def fake_title(question):
        title_requests.append(question)
        return "Multi-turn test"

    monkeypatch.setattr(main, "run_full_council", fake_council)
    monkeypatch.setattr(main, "generate_conversation_title", fake_title)
    client = TestClient(main.app)
    a = client.post("/api/conversations", json={}).json()
    b = client.post("/api/conversations", json={}).json()

    first = client.post(f"/api/conversations/{a['id']}/message",
                        json={"content": "Is Python suitable?"})
    assert first.status_code == 200
    second = client.post(f"/api/conversations/{a['id']}/message",
                         json={"content": "What about JavaScript?"})
    assert second.status_code == 200
    other = client.post(f"/api/conversations/{b['id']}/message",
                        json={"content": "Completely unrelated"})
    assert other.status_code == 200

    assert prompts[0][0] == "Is Python suitable?"
    assert "Is Python suitable?" in prompts[1][0]
    assert "Final answer #1" in prompts[1][0]
    assert "What about JavaScript?" in prompts[1][0]
    assert "Raw Stage 1 internal details" not in prompts[1][0]
    assert "Raw Stage 2 private review" not in prompts[1][0]
    assert prompts[2][0] == "Completely unrelated"
    assert title_requests == ["Is Python suitable?", "Completely unrelated"]
    assert len(storage.get_conversation(a["id"])["messages"]) == 4
    assert [m["content"] for m in storage.get_conversation(a["id"])["messages"]
            if m["role"] == "user"] == ["Is Python suitable?", "What about JavaScript?"]
    assert storage.get_conversation(a["id"])["messages"][-1]["stage3"]["response"] == "Final answer #2"


def test_sse_followup_uses_same_context_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    prompts = []

    async def fake_council(query, **kwargs):
        prompts.append(query)
        return _council_result(f"Final answer #{len(prompts)}")

    async def fake_title(question):
        return "Stream title"

    monkeypatch.setattr(main, "run_full_council", fake_council)
    monkeypatch.setattr(main, "generate_conversation_title", fake_title)
    client = TestClient(main.app)
    conversation = client.post("/api/conversations", json={}).json()
    endpoint = f"/api/conversations/{conversation['id']}/message/stream"
    assert client.post(endpoint, json={"content": "Original question"}).status_code == 200
    response = client.post(endpoint, json={"content": "Follow-up question"})
    assert response.status_code == 200
    assert "stage3_complete" in response.text
    assert prompts[0] == "Original question"
    assert "Original question" in prompts[1]
    assert "Final answer #1" in prompts[1]
    assert prompts[1].endswith("Follow-up question")
    messages = storage.get_conversation(conversation["id"])["messages"]
    assert len(messages) == 4
    assert messages[2] == {"role": "user", "content": "Follow-up question"}
    assert messages[3]["stage3"]["response"] == "Final answer #2"
