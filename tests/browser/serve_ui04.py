"""UI-04 provider fixture: real engine HTTP transport, never a Council/SSE stub."""
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from serve import serve

# Independent Quick expectations from the pinned Amiable pool/tier contract.
MODELS = ["google/gemini-3.5-flash-lite", "deepseek/deepseek-v4-flash", "anthropic/claude-haiku-4.5"]
CHAIRMAN = "anthropic/claude-haiku-4.5"
TITLE_MODEL = "google/gemini-2.5-flash"
KEY = "ui04-test-only-not-a-real-key"
QUESTION = "UI04: Should we preserve the original archive?"
TITLE = "Archive Retention Decision"
FINAL = "Preserve the original archive and verify its integrity."
LABELS = ["Response A", "Response B", "Response C"]
REVIEW_INTRO = "All candidates support archive retention."
REVIEW = REVIEW_INTRO + "\n```json\n" + json.dumps({
    "ranking": LABELS, "scores": dict(zip(LABELS, (9, 8, 7))),
}) + "\n```"
STAGES = ("stage1", "stage2", "stage3")
JOURNAL = "provider-events.jsonl"


def candidate(model):
    return f"Candidate {MODELS.index(model) + 1}: preserve the original archive."


def record(phase, stage, model):
    with Path(JOURNAL).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"phase": phase, "stage": stage, "model": model}) + "\n")


def classify(request):
    assert request.method == "POST"
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers.get("authorization") == f"Bearer {KEY}"
    body = json.loads(request.content)
    model = body["model"]
    prompt = body["messages"][-1]["content"]
    assert QUESTION in prompt
    if prompt == QUESTION:
        assert model in MODELS
        return "stage1", model, candidate(model)
    if "<responses_to_evaluate>" in prompt:
        assert model in MODELS and all(candidate(m) in prompt for m in MODELS)
        return "stage2", model, REVIEW
    if prompt.startswith("You are the Chairman of an LLM Council."):
        assert model == CHAIRMAN and all(candidate(m) in prompt for m in MODELS)
        assert REVIEW in prompt and "AGGREGATE RANKINGS" in prompt
        return "stage3", model, FINAL
    assert prompt.startswith("Generate a very short title") and model == TITLE_MODEL
    return "title", model, TITLE


async def controlled_provider(_transport, request):
    try:
        stage, model, content = classify(request)
    except (AssertionError, KeyError, ValueError, TypeError, IndexError):
        record("rejected", "unexpected", "unknown")
        raise AssertionError("UI04: unexpected provider request") from None
    record("request", stage, model)
    if stage in STAGES:
        # A browser assertion, not elapsed time, releases each real HTTP stage.
        deadline = asyncio.get_running_loop().time() + 25
        while not Path(f"release-{stage}").is_file():
            if asyncio.get_running_loop().time() >= deadline:
                record("rejected", stage, model)
                raise AssertionError("UI04: provider gate timed out")
            await asyncio.sleep(0.02)
    record("response", stage, model)
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0125},
    })


if __name__ == "__main__":
    if not os.environ.get("LLM_COUNCIL_UI_DATA_DIR") or not os.environ.get("LLM_COUNCIL_CONFIG"):
        raise RuntimeError("Run the isolated browser harness, not the server directly")
    serve(sys.argv[1], async_transport=controlled_provider)
