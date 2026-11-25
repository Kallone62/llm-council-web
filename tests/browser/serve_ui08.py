"""UI-08: record actual provider prompts for five interleaved Council rounds."""
import json
import os
import sys
from pathlib import Path

import httpx

from serve import serve
from serve_ui04 import CHAIRMAN, JOURNAL, KEY, MODELS, REVIEW, TITLE_MODEL

ROUND_FILE = "ui08-round.txt"
QUESTIONS = {
    "a-first": 'UI08: Retain "BLUE_ARCHIVE" for İzmir.',
    "b-first": 'UI08: Retain "GREEN_ARCHIVE" for Bursa.',
    "a-failed": "UI08: Should RED_ARCHIVE replace the original?",
    "a-follow": "UI08: Which archive did we decide to retain?",
    "b-follow": "UI08: Which archive did we decide to retain?",
}
FINALS = {
    "a-first": "Retain BLUE_ARCHIVE until verification succeeds.",
    "b-first": "Retain GREEN_ARCHIVE until verification succeeds.",
    "a-failed": "",
    "a-follow": "The previous decision was to retain BLUE_ARCHIVE.",
    "b-follow": "The previous decision was to retain GREEN_ARCHIVE.",
}
TITLES = {"a": "Blue Archive Decision", "b": "Green Archive Decision"}
ERROR = "Chairman returned no usable final synthesis."


def candidate(round_id, model):
    return f"Candidate {MODELS.index(model) + 1} for {round_id}: retain the original archive."


def classify(round_id, request):
    assert (request.method, str(request.url), request.headers.get("authorization")) == (
        "POST", "https://openrouter.ai/api/v1/chat/completions", f"Bearer {KEY}")
    body = json.loads(request.content)
    model, prompt = body["model"], body["messages"][-1]["content"]
    if prompt.startswith("Generate a very short title"):
        assert round_id.endswith("-first") and model == TITLE_MODEL
        return "title", model, prompt, TITLES[round_id[0]]
    if "<responses_to_evaluate>" in prompt:
        assert model in MODELS and all(candidate(round_id, member) in prompt for member in MODELS)
        return "stage2", model, prompt, REVIEW
    if prompt.startswith("You are the Chairman of an LLM Council."):
        assert model == CHAIRMAN and all(candidate(round_id, member) in prompt for member in MODELS)
        assert REVIEW in prompt and "AGGREGATE RANKINGS" in prompt
        return "stage3", model, prompt, FINALS[round_id]
    # Do not reject a wrong history here. Preserve the actual prompt so the
    # independent browser oracle can diagnose missing/foreign context exactly.
    assert model in MODELS
    return "stage1", model, prompt, candidate(round_id, model)


def journal(round_id, stage, model, prompt):
    with Path(JOURNAL).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"round": round_id, "stage": stage, "model": model, "prompt": prompt}, ensure_ascii=False) + "\n")


async def controlled_ui08_provider(_transport, request):
    try:
        round_id = Path(ROUND_FILE).read_text(encoding="utf-8")
        stage, model, prompt, content = classify(round_id, request)
    except (AssertionError, KeyError, ValueError, TypeError, IndexError, OSError):
        journal("unknown", "rejected", "unknown", "")
        raise AssertionError("UI08: unexpected provider request") from None
    journal(round_id, stage, model, prompt)
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0125}})


if __name__ == "__main__":
    if not os.environ.get("LLM_COUNCIL_UI_DATA_DIR") or not os.environ.get("LLM_COUNCIL_CONFIG"):
        raise RuntimeError("Run the isolated browser harness, not the server directly")
    serve(sys.argv[1], async_transport=controlled_ui08_provider)
