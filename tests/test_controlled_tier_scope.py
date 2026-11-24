"""Audit the pinned compatibility and native consult paths at the HTTP boundary."""
import asyncio
import json
import os
import socket
from collections import Counter

import httpx
import pytest
import yaml
from llm_council import council, consult_council
from llm_council.tier_contract import create_tier_contract
from llm_council.unified_config import reload_config


# Independent, pinned tier contract values; not derived from either result.
TIERS = {"quick": (20, 30), "balanced": (45, 90),
         "high": (90, 180), "reasoning": (300, 600)}
MODELS = ["google/gemini-3.5-flash-lite", "deepseek/deepseek-v4-flash",
          "anthropic/claude-haiku-4.5"]
CHAIRMAN = "anthropic/claude-haiku-4.5"
POOLS = {
    "quick": (MODELS, CHAIRMAN),
    "balanced": (["openai/gpt-5.6-luna", "anthropic/claude-sonnet-5",
                  "deepseek/deepseek-v4-flash", "google/gemini-3.7-flash"], "anthropic/claude-sonnet-5"),
    "high": (["openai/gpt-5.6-sol", "anthropic/claude-opus-5",
              "deepseek/deepseek-v4-pro-0813", "z-ai/glm-5.3"], "openai/gpt-5.6-sol"),
    "reasoning": (["openai/gpt-5.6-sol-pro", "anthropic/claude-opus-5",
                   "z-ai/glm-5.3", "deepseek/deepseek-v4-pro-0813"], "anthropic/claude-opus-5"),
}
QUESTION = "ENG26: Should we preserve the archive?"
FINAL = "Preserve the archive."
PARTIAL = "Partial synthesis: preserve the archive."


def configure(tmp_path, monkeypatch, tier, short_deadline):
    for name in tuple(os.environ):
        if name.startswith(("LLM_COUNCIL_", "OTEL_", "POSTHOG_")) or name.lower().endswith("_proxy"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "eng26-test-only-not-a-real-key")
    monkeypatch.setenv("LLM_COUNCIL_PERFORMANCE_TRACKING", "false")
    monkeypatch.setenv("LLM_COUNCIL_EARLY_CONSENSUS", "false")
    monkeypatch.setenv("LLM_COUNCIL_MODEL_INTELLIGENCE", "false")
    monkeypatch.setenv("LLM_COUNCIL_PERFORMANCE_STORE", str(tmp_path / "performance.jsonl"))
    config = {"tiers": {"default": tier},
              "council": {"models": POOLS[tier][0], "style_normalization": False},
              "gateways": {"default": "openrouter", "fallback": {"enabled": False}},
              "cache": {"enabled": False},
              "evaluation": {"bias": {"persistence_enabled": False}}}
    if short_deadline:
        config["timeouts"] = {"quick": {"total": 1000, "per_model": 2000}}
    path = tmp_path / "tier.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(path))
    reload_config()


def provider(monkeypatch, models, chairman, stage1_timeout, stage_timeout, *, stall=False):
    calls, cancelled = [], []

    def deny(*args, **kwargs):
        pytest.fail("Unexpected external network in tier scope acceptance")

    monkeypatch.setattr(socket, "getaddrinfo", deny)
    original_connect = socket.socket.connect

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            deny()
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    labels = [f"Response {chr(65 + i)}" for i in range(len(models))]
    review = "```json\n" + json.dumps({"ranking": labels, "scores": dict.fromkeys(labels, 8)}) + "\n```"

    async def handle(_transport, request):
        assert request.method == "POST"
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer eng26-test-only-not-a-real-key"
        body = json.loads(request.content)
        model, prompt = body["model"], body["messages"][-1]["content"]
        assert QUESTION in prompt
        if prompt == QUESTION:
            stage, timeout, content = "stage1", stage1_timeout, f"Candidate {model}: preserve the archive."
            assert model in models
        elif "<responses_to_evaluate>" in prompt:
            stage, timeout, content = "stage2", stage_timeout, review
            assert model in models
            assert all(f"Candidate {m}: preserve the archive." in prompt for m in models)
        elif prompt.startswith("You are the Chairman of an LLM Council."):
            stage, timeout, content = "stage3", stage_timeout, FINAL
            assert model == chairman and "AGGREGATE RANKINGS" in prompt
        elif prompt.startswith("You are synthesizing multiple AI responses"):
            stage, timeout, content = "fallback", stage1_timeout, PARTIAL
            assert model == chairman and "PARTIAL synthesis" in prompt
        else:
            pytest.fail(f"Unexpected tier scope prompt: {prompt[:120]}")
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), timeout)
        calls.append((stage, model))
        if stall and stage == "stage2":
            try:
                await asyncio.sleep(1.3)
            except asyncio.CancelledError:
                cancelled.append(model)
                raise
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
                                         "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0125}})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return calls, cancelled


@pytest.mark.parametrize("tier", TIERS)
def test_native_consult_vs_compatibility(tmp_path, monkeypatch, tier):
    configure(tmp_path, monkeypatch, tier, False)
    per_model, total = TIERS[tier]
    contract = create_tier_contract(tier)
    assert contract.per_model_timeout_ms == per_model * 1000
    assert contract.deadline_ms == total * 1000
    assert contract.requires_peer_review is (tier != "quick")
    assert contract.requires_verifier is (tier == "quick")
    models, chairman = POOLS[tier]
    assert contract.allowed_models == models and contract.aggregator_model == chairman
    expected = Counter([("stage1", m) for m in models] + [("stage2", m) for m in models] + [("stage3", chairman)])
    calls, _ = provider(monkeypatch, models, chairman, 120, max(120, per_model))
    stage1, stage2, stage3, metadata = asyncio.run(council.run_full_council(
        QUESTION, bypass_cache=True, per_model_timeout=per_model))
    assert Counter(calls) == expected
    assert len(stage1) == len(stage2) == len(models)
    assert stage3["response"] == FINAL and stage3["model"] == chairman
    assert "tier" not in metadata
    calls, _ = provider(monkeypatch, models, chairman, per_model, max(120, per_model))
    result = asyncio.run(consult_council(QUESTION, confidence=tier, bypass_cache=True))
    assert Counter(calls) == expected  # Quick consult also runs full peer review.
    assert result.synthesis == FINAL
    assert result.metadata["tier"] == tier and result.metadata["status"] == "complete"
    assert result.metadata["synthesis_type"] == "full"
    assert set(result.model_responses) == set(models)
    assert all(value["status"] == "ok" for value in result.model_responses.values())


def test_native_consult_deadline_is_not_compatibility_deadline(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, "quick", True)
    calls, cancelled = provider(monkeypatch, MODELS, CHAIRMAN, 120, 120, stall=True)
    stage1, stage2, stage3, _ = asyncio.run(council.run_full_council(
        QUESTION, bypass_cache=True, per_model_timeout=2))
    assert len(stage1) == len(stage2) == 3 and stage3["response"] == FINAL
    assert not cancelled and calls[-1] == ("stage3", CHAIRMAN)
    calls, cancelled = provider(monkeypatch, MODELS, CHAIRMAN, 2, 120, stall=True)
    result = asyncio.run(consult_council(QUESTION, confidence="quick", bypass_cache=True))
    assert result.metadata["status"] == "partial" and result.metadata["synthesis_type"] == "partial"
    assert result.metadata["completed_models"] == 3 and result.synthesis == PARTIAL
    assert Counter(cancelled) == Counter(MODELS)
    assert Counter(calls) == Counter([("stage1", m) for m in MODELS] + [("stage2", m) for m in MODELS] + [("fallback", CHAIRMAN)])
