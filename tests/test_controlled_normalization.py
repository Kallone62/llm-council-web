"""Native Stage 1.5 policy, actual review inputs, and preserved original answers."""
import html
import json
import re
from collections import Counter

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from llm_council import council
from llm_council.unified_config import get_config, reload_config

from test_controlled_profiles import (
    KEY, PROFILES, TITLE, TITLE_MODEL, decode_response, deny_external_network,
    isolated_profile_config, review_text,
)

MODELS, CHAIRMAN, _, _ = PROFILES["high"]
NORMALIZER = "google/gemini-3.5-flash-lite"
QUESTION = "ENG16: Which archive integrity checks should be performed?"
FINAL = "Verify all archive checks before migration."
REVIEW = review_text(MODELS)
NEUTRAL = {model: f"Check {i}: verify archive integrity before migration." for i, model in enumerate(MODELS, 1)}


def original_responses(style):
    originals = {model: f"Check {i}: validate archive integrity before migration." for i, model in enumerate(MODELS, 1)}
    first = MODELS[0]
    if style == "markdown":
        originals[first] = "# Integrity\n" + originals[first]
    elif style == "length":
        originals[first] += " Check the backup and compare the stored checksum." * 10
    elif style == "preamble":
        originals[first] = "Certainly! " + originals[first]
    elif style == "code":
        originals[first] += "\n```\nsha256sum archive\n```"
    return originals


def install_normalizer(monkeypatch, originals, expected_review, failure):
    calls, normalized, review_inputs = [], [], []
    deny_external_network(monkeypatch, "Unexpected network in Stage 1.5 acceptance")

    async def handle(_transport, request):
        assert (request.method, str(request.url), request.headers.get("authorization")) == (
            "POST", "https://openrouter.ai/api/v1/chat/completions", f"Bearer {KEY}")
        body = json.loads(request.content)
        model, prompt = body["model"], body["messages"][-1]["content"]
        if prompt.startswith("Rewrite the following text to have a neutral, consistent style"):
            stage, timeout = "normalize", 60.0
            assert model == NORMALIZER
            assert "preserving ALL content and meaning exactly" in prompt
            assert "Do NOT add or remove any substantive content" in prompt
            assert "Remove any AI-assistant preambles" in prompt
            original = prompt.split("\nOriginal text:\n", 1)[1].removesuffix("\n\nRewritten text:")
            author = next(m for m, value in originals.items() if value == original)
            normalized.append(author)
            answer = NEUTRAL[author]
            if failure and author == MODELS[0]:
                assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), timeout)
                calls.append((stage, model))
                return httpx.Response(401, json={"error": {"message": "Fixture normalization unavailable"}})
        elif prompt.startswith("Generate a very short title"):
            stage, timeout, answer = "title", 30.0, TITLE
            assert QUESTION in prompt and model == TITLE_MODEL
        elif prompt == QUESTION:
            stage, timeout, answer = "stage1", 120.0, originals[model]
        elif "<responses_to_evaluate>" in prompt:
            stage, timeout, answer = "stage2", 120.0, REVIEW
            assert model in MODELS and QUESTION in prompt
            presented = {f"Response {label}": html.unescape(text) for label, text in re.findall(
                r'<candidate_response id="([A-D])">\n(.*?)\n</candidate_response>', prompt, re.DOTALL)}
            assert len(presented) == 4 and Counter(presented.values()) == Counter(expected_review.values())
            review_inputs.append(presented)
        else:
            stage, timeout, answer = "stage3", 120.0, FINAL
            assert model == CHAIRMAN and QUESTION in prompt
            assert prompt.startswith("You are the Chairman of an LLM Council.")
            assert all(f"Model: {m}\nResponse: {value}" in prompt for m, value in originals.items())
            assert all(value not in prompt for value in NEUTRAL.values())
            assert "AGGREGATE RANKINGS" in prompt
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), timeout)
        calls.append((stage, model))
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0125},
        })

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return calls, normalized, review_inputs


@pytest.mark.parametrize("mode,style,active,failure", [
    ("off", "markdown", False, False), ("on", "uniform", True, False),
    ("auto", "uniform", False, False), ("auto", "markdown", True, False),
    ("auto", "length", True, False), ("auto", "preamble", True, False),
    ("auto", "code", True, False), ("on", "markdown", True, True),
], ids=["off-diverse", "on-uniform", "auto-uniform", "auto-markdown", "auto-length",
        "auto-preamble", "auto-code", "normalizer-error-fallback"])
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_native_style_normalization(isolated_profile_config, monkeypatch, mode, style, active, failure, stream):
    from backend import main, storage

    assert main.run_full_council is council.run_full_council
    originals = original_responses(style)
    expected_review = dict(NEUTRAL if active else originals)
    if failure:
        expected_review[MODELS[0]] = originals[MODELS[0]]
    calls, normalized, review_inputs = install_normalizer(monkeypatch, originals, expected_review, failure)
    native_mode = {"off": False, "on": True, "auto": "auto"}[mode]
    with TestClient(main.app) as client:
        previous = "on" if mode != "on" else "off"
        seeded = client.put("/api/settings", json={"profile": "high", "style_normalization": previous})
        assert seeded.status_code == 200 and seeded.json()["style_normalization"] == previous
        settings = client.put("/api/settings", json={"profile": "high", "style_normalization": mode})
        assert settings.status_code == 200 and settings.json()["style_normalization"] == mode
        saved = yaml.safe_load(isolated_profile_config.read_text(encoding="utf-8"))
        assert saved["council"]["style_normalization"] == native_mode
        reload_config()
        assert get_config().council.style_normalization == native_mode
        assert get_config().council.normalizer_model == NORMALIZER
        assert client.get("/api/settings").json()["style_normalization"] == mode
        assert not calls
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        path = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = decode_response(client.post(path, json={"content": QUESTION}), stream)
        expected_normalizations = 4 if active else 0
        assert Counter(calls) == Counter([("title", TITLE_MODEL), ("stage3", CHAIRMAN)] + [
            (stage, model) for stage in ("stage1", "stage2") for model in MODELS] + [("normalize", NORMALIZER)] * expected_normalizations)
        assert [stage for stage, _ in calls if stage != "title"] == ["stage1"] * 4 + ["normalize"] * expected_normalizations + ["stage2"] * 4 + ["stage3"]
        assert Counter(normalized) == Counter(MODELS if active else [])
        assert len(result["stage1"]) == 4 and {r["model"]: r["response"] for r in result["stage1"]} == originals
        assert all("original_response" not in r for r in result["stage1"])
        assert len(result["stage2"]) == 4 and {r["model"] for r in result["stage2"]} == set(MODELS)
        assert all(r["ranking"] == REVIEW for r in result["stage2"])
        assert result["stage3"] == {"model": CHAIRMAN, "response": FINAL}
        metadata = result["metadata"]
        assert metadata["config"]["style_normalization"] == native_mode
        labels = metadata["label_to_model"]
        expected_labels = {label: expected_review[entry["model"]] for label, entry in labels.items()}
        assert len(review_inputs) == 4 and all(presented == expected_labels for presented in review_inputs)
        assert metadata["peer_review"]["state"] == "completed" and metadata["peer_review"]["effective_votes"] == 12
        successes = expected_normalizations - int(failure)
        usage = metadata["usage"]
        stage_usage = usage["by_stage"]["stage1_5"]
        assert [stage_usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")] == [11 * successes, 7 * successes, 18 * successes]
        assert stage_usage.get("cost_usd", 0) == pytest.approx(0.0125 * successes)
        assert usage["total"]["total_tokens"] == 18 * (9 + successes)
        assert usage["total"]["cost_usd"] == pytest.approx(0.0125 * (9 + successes))
        assert metadata.get("status") not in ("partial", "failed")
        disk = storage.get_conversation(cid)
        assert disk["title"] == TITLE
        assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **result}]
        assert client.get(f"/api/conversations/{cid}").json() == disk
