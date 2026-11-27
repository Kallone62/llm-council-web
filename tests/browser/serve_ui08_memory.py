"""UI-08 rolling memory: controlled HTTP only, with an observable summary gate."""
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from serve import serve
from serve_ui04 import CHAIRMAN, FINAL, KEY, MODELS, REVIEW, TITLE_MODEL, candidate

ROUND = "memory-round.txt"
JOURNAL = "memory-provider.jsonl"
RELEASE = "release-summary"
LONG_QUESTION = " ".join(["Retain the original record."] * 250)
LONG_ANSWER = "Confirmed: preserve the entire original record."
OLD_MEMORY = {"version": 3, "summary": "Keep the original BLUE archive.",
              "covered_turns": 1, "model": TITLE_MODEL}
SUMMARY = "Keep the original BLUE archive and preserve the entire original record."
NEW_MEMORY = {"version": 3, "summary": SUMMARY, "covered_turns": 2, "model": TITLE_MODEL}
QUESTIONS = {"summary": "UI08 memory: Which archive is retained?",
             "reuse": "UI08 memory: What is the next step?"}


def classify(request):
    assert (request.method, str(request.url), request.headers.get("authorization")) == (
        "POST", "https://openrouter.ai/api/v1/chat/completions", f"Bearer {KEY}")
    body = json.loads(request.content)
    model, prompt = body["model"], body["messages"][-1]["content"]
    if body["messages"][0]["role"] == "system" and "You maintain concise, faithful memory" in body["messages"][0]["content"]:
        assert model == TITLE_MODEL
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 45.0)
        return "memory", model, prompt, SUMMARY
    if "<responses_to_evaluate>" in prompt:
        assert model in MODELS and all(candidate(m) in prompt for m in MODELS)
        return "stage2", model, prompt, REVIEW
    if prompt.startswith("You are the Chairman of an LLM Council."):
        assert model == CHAIRMAN and all(candidate(m) in prompt for m in MODELS)
        assert REVIEW in prompt and "AGGREGATE RANKINGS" in prompt
        return "stage3", model, prompt, FINAL
    assert model in MODELS
    return "stage1", model, prompt, candidate(model)


def journal(round_id, stage, model, prompt):
    with Path(JOURNAL).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"round": round_id, "stage": stage, "model": model, "prompt": prompt}) + "\n")


async def provider(_transport, request):
    try:
        round_id = Path(ROUND).read_text(encoding="utf-8")
        assert round_id in QUESTIONS
        stage, model, prompt, content = classify(request)
    except (AssertionError, KeyError, ValueError, TypeError, IndexError, OSError):
        journal("unknown", "rejected", "unknown", "")
        raise AssertionError("UI08-memory: unexpected provider request") from None
    journal(round_id, stage, model, prompt)
    if stage == "memory":
        deadline = asyncio.get_running_loop().time() + 25
        while not Path(RELEASE).is_file():
            assert asyncio.get_running_loop().time() < deadline, "UI08-memory: summary gate timed out"
            await asyncio.sleep(.02)
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0125}})


if __name__ == "__main__":
    if not os.environ.get("LLM_COUNCIL_UI_DATA_DIR") or not os.environ.get("LLM_COUNCIL_CONFIG"):
        raise RuntimeError("Run the isolated browser harness, not the server directly")
    serve(sys.argv[1], async_transport=provider)
