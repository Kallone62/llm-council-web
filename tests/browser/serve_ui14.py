"""UI-14: real native Free round, controlled provider HTTP and explicit stage gates."""
import asyncio
import json
import sys
from pathlib import Path

import httpx

from serve import serve, deny_provider
from serve_ui04 import REVIEW, FINAL

MODELS = ["google/gemma-4-31b-it:free", "qwen/qwen3.8-27b:free",
          "nvidia/nemotron-3-super-120b-a12b:free"]
KEY = "ui14-original-test-only"
NEW_KEY = "ui14-replacement-test-only"
QUESTION = "UI14: Preserve the original archive?"
JOURNAL = "ui14-provider.jsonl"
CATALOG = "https://openrouter.ai/api/v1/models"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def record(**row):
    with Path(JOURNAL).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def catalog(_transport, request):
    if (request.method, str(request.url), request.headers.get("authorization")) != (
            "GET", CATALOG, f"Bearer {KEY}"):
        deny_provider()
    record(phase="catalog", url=CATALOG)
    entries = [{"id": m, "pricing": {"prompt": "0", "completion": "0", "request": "0"}} for m in MODELS]
    entries.append({"id": "google/gemini-2.5-flash", "pricing": {"prompt": "1", "completion": "1", "request": "1"}})
    return httpx.Response(200, json={"data": entries})


def candidate(model):
    return f"Candidate {MODELS.index(model) + 1}: preserve the original archive."


async def provider(_transport, request):
    body = json.loads(request.content)
    model = body["model"]
    authorized = request.headers.get("authorization") == f"Bearer {KEY}"
    record(phase="request", url=str(request.url), model=model, original_key=authorized)
    assert request.method == "POST" and str(request.url) == ENDPOINT and authorized
    assert model in MODELS, "UI14: non-Free provider request"
    prompt = body["messages"][-1]["content"]
    assert QUESTION in prompt
    if prompt == QUESTION:
        stage, content = "stage1", candidate(model)
    elif "<responses_to_evaluate>" in prompt:
        assert all(candidate(m) in prompt for m in MODELS)
        stage, content = "stage2", REVIEW
    else:
        assert prompt.startswith("You are the Chairman of an LLM Council.")
        assert model == MODELS[0] and all(candidate(m) in prompt for m in MODELS)
        assert REVIEW in prompt and "AGGREGATE RANKINGS" in prompt
        stage, content = "stage3", FINAL
    record(phase="entered", stage=stage, model=model)
    if stage in ("stage1", "stage3"):
        deadline = asyncio.get_running_loop().time() + 25
        while not Path(f"release-{stage}").is_file():
            assert asyncio.get_running_loop().time() < deadline, "UI14: provider gate timed out"
            await asyncio.sleep(.02)
    record(phase="response", stage=stage, model=model)
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0},
    })


def install_catalog(app):
    # Only the exact catalog request is permitted at the synchronous HTTP boundary.
    httpx.HTTPTransport.handle_request = catalog
    return app


if __name__ == "__main__":
    serve(sys.argv[1], async_transport=provider, wrap_app=install_catalog)
