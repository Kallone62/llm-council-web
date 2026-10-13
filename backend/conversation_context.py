"""A small, bounded conversation-context adapter for Amiable's stateless API.

Only completed exchanges are included. We pass earlier *user questions* and
*Chairman final answers*, not the much larger Stage 1 / Stage 2 evaluations.
The original conversation JSON always stores user messages exactly as typed.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

MAX_HISTORY_TURNS = 3
MAX_HISTORY_CHARS = 6000
MAX_QUESTION_CHARS = 1000
MAX_ANSWER_CHARS = 1600


def build_council_query(
    messages: Sequence[Mapping[str, Any]], user_query: str
) -> str:
    """Attach recent completed exchanges to a new question, if any exist.

    First-turn calls remain byte-for-byte unchanged for backward compatibility.
    Pending or failed exchanges are not treated as prior Council decisions.
    """
    completed = []
    pending_question = None

    for message in messages:
        if message.get("role") == "user":
            content = message.get("content")
            pending_question = content if isinstance(content, str) and content.strip() else None
        elif message.get("role") == "assistant":
            stage3 = message.get("stage3")
            answer = stage3.get("response") if isinstance(stage3, dict) else None
            if pending_question and isinstance(answer, str) and answer.strip():
                completed.append((pending_question, answer))
            pending_question = None

    if not completed:
        return user_query

    # Prefer the most recent complete exchanges under a fixed context budget.
    selected = []
    used = 0
    for question, answer in reversed(completed):
        entry = (
            "Previous user question: "
            + json.dumps(question[:MAX_QUESTION_CHARS], ensure_ascii=False)
            + "\nPrevious Council final answer: "
            + json.dumps(answer[:MAX_ANSWER_CHARS], ensure_ascii=False)
        )
        if used + len(entry) > MAX_HISTORY_CHARS:
            break
        selected.append(entry)
        used += len(entry)
        if len(selected) == MAX_HISTORY_TURNS:
            break

    if not selected:
        return user_query

    return (
        "Conversation context from earlier *completed* Council rounds "
        "(background only, not new questions):\n\n"
        + "\n\n".join(reversed(selected))
        + "\n\nCurrent user question for this Council round:\n"
        + user_query
    )
