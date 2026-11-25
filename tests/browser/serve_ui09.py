"""UI-09: only the real native memory HTTP boundary is controlled."""
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from serve import serve
from stream_observer_ui09 import observe_stream
from serve_ui04 import CHAIRMAN, KEY, TITLE_MODEL

LONG_QUESTION = "Retain the original archive. " * 250
OLD_ANSWER = "Preserve the original archive."
QUESTION = "UI09: Can we discard the original archive?"
JOURNAL = "ui09-provider.jsonl"
HTTP_ERROR = ("A free OpenRouter API key is still required for Free models. "
              "Add it in Settings; no credit purchase is required for "
              "available free-tier requests.")
SSE_ERROR = "Could not prepare AI conversation memory. Please retry."


async def memory_failure(_transport, request):
    try:
        if (request.method, str(request.url), request.headers.get("authorization")) != (
                "POST", "https://openrouter.ai/api/v1/chat/completions", f"Bearer {KEY}"):
            raise ValueError("Unexpected provider request target or authorization")
        body = json.loads(request.content)
        if body["model"] != TITLE_MODEL:
            raise ValueError("Unexpected memory model")
        if "You maintain concise, faithful memory" not in body["messages"][0]["content"]:
            raise ValueError("Unexpected memory instructions")
        if json.loads(body["messages"][-1]["content"]) != {
            "previous_memory": None,
            "completed_exchanges_since_memory": [
                {"user_question": LONG_QUESTION, "chairman_final_answer": OLD_ANSWER}],
        }:
            raise ValueError("Unexpected memory input")
        if request.extensions["timeout"] != dict.fromkeys(("connect", "read", "write", "pool"), 45.0):
            raise ValueError("Unexpected memory timeout")
    except (KeyError, ValueError, TypeError, IndexError):
        with Path(JOURNAL).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"stage": "unexpected"}) + "\n")
        raise AssertionError("UI09: unexpected provider request") from None
    with Path(JOURNAL).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"stage": "memory", "model": TITLE_MODEL}) + "\n")
    deadline = asyncio.get_running_loop().time() + 25
    while not Path("release-memory").is_file():
        assert asyncio.get_running_loop().time() < deadline, "UI09: memory release timeout"
        await asyncio.sleep(.02)
    # A successful HTTP response with no usable summary causes the production
    # memory helper to raise ContextSummaryError; no synthetic SSE is injected.
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": ""}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 0, "total_tokens": 11, "cost": 0.0}})


if __name__ == "__main__":
    if not os.environ.get("LLM_COUNCIL_UI_DATA_DIR") or not os.environ.get("LLM_COUNCIL_CONFIG"):
        raise RuntimeError("Run the isolated browser harness, not the server directly")
    serve(sys.argv[1], async_transport=memory_failure, wrap_app=observe_stream)
