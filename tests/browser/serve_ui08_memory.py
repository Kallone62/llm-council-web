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
    if (request.method, str(request.url), request.headers.get("authorization")) != (
            "POST", "https://openrouter.ai/api/v1/chat/completions", f"Bearer {KEY}"):
        raise ValueError("Unexpected provider request target or authorization")
    body = json.loads(request.content)
    model, prompt = body["model"], body["messages"][-1]["content"]
    if body["messages"][0]["role"] == "system" and "You maintain concise, faithful memory" in body["messages"][0]["content"]:
        if model != TITLE_MODEL:
            raise ValueError("Unexpected summary model")
        if request.extensions["timeout"] != dict.fromkeys(("connect", "read", "write", "pool"), 45.0):
            raise ValueError("Unexpected summary timeout")
        return "memory", model, prompt, SUMMARY
    if "<responses_to_evaluate>" in prompt:
        if model not in MODELS or not all(candidate(m) in prompt for m in MODELS):
            raise ValueError("Unexpected review model or candidates")
        return "stage2", model, prompt, REVIEW
    if prompt.startswith("You are the Chairman of an LLM Council."):
        if model != CHAIRMAN or not all(candidate(m) in prompt for m in MODELS):
            raise ValueError("Unexpected Chairman model or candidates")
        if REVIEW not in prompt or "AGGREGATE RANKINGS" not in prompt:
            raise ValueError("Missing Chairman review or aggregate rankings")
        return "stage3", model, prompt, FINAL
    if model not in MODELS:
        raise ValueError("Unexpected candidate model")
    return "stage1", model, prompt, candidate(model)


def journal(round_id, stage, model, prompt):
    with Path(JOURNAL).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"round": round_id, "stage": stage, "model": model, "prompt": prompt}) + "\n")


async def provider(_transport, request):
    try:
        round_id = Path(ROUND).read_text(encoding="utf-8")
        if round_id not in QUESTIONS:
            raise ValueError("Unexpected rolling-memory round")
        stage, model, prompt, content = classify(request)
    except (AssertionError, KeyError, ValueError, TypeError, IndexError, OSError):
        journal("unknown", "rejected", "unknown", "")
        raise AssertionError("UI08-memory: unexpected provider request") from None
    journal(round_id, stage, model, prompt)
    if stage == "memory":
        deadline = asyncio.get_running_loop().time() + 25
        while not Path(RELEASE).is_file():
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("UI08-memory: summary gate timed out")
            await asyncio.sleep(.02)
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0125}})


if __name__ == "__main__":
    if not os.environ.get("LLM_COUNCIL_UI_DATA_DIR") or not os.environ.get("LLM_COUNCIL_CONFIG"):
        raise RuntimeError("Run the isolated browser harness, not the server directly")
    serve(sys.argv[1], async_transport=provider)
