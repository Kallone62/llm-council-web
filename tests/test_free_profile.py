"""Offline regression tests for the OpenRouter-only Free onboarding profile."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend import main, storage, conversation_context
from backend.free_profile import (
    FREE_MODELS, FREE_CHAIRMAN, FREE_SUMMARY_MODEL, NATIVE_FREE_TIER,
)
from llm_council.unified_config import get_config, reload_config


@pytest.fixture()
def free_client(tmp_path, monkeypatch):
    from backend import config as app_config

    path = tmp_path / "llm_council.yaml"
    original = __import__("os").environ.get("LLM_COUNCIL_CONFIG")
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(path))
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    # Always begin from a minimal native engine config to avoid surprises from
    # a developer's locally customized defaults.
    path.write_text("tiers:\n  default: high\n", encoding="utf-8")
    reload_config()
    client = TestClient(main.app)
    yield client, path
    if original is None:
        monkeypatch.delenv("LLM_COUNCIL_CONFIG", raising=False)
    else:
        monkeypatch.setenv("LLM_COUNCIL_CONFIG", original)
    reload_config()


def _select_free(client):
    r = client.put("/api/settings", json={
        "profile": "free",
        "use_profile_models": True,
        "chairman": None,
        "style_normalization": "off",
        "rubric_enabled": False,
        "bias_audit_enabled": False,
        "safety_enabled": False,
    })
    assert r.status_code == 200, r.text
    return r.json()


def _answer(answer="Free answer"):
    return (
        [{"model": FREE_MODELS[0], "response": "Opinion 1"}],
        [{"model": FREE_MODELS[1], "ranking": "Opinion 1"}],
        {"model": FREE_CHAIRMAN, "response": answer},
        {"usage": {"total": {"total_tokens": 10}}},
    )


def test_free_selection_saves_native_quick_and_exposes_free_label(free_client):
    client, path = free_client
    result = _select_free(client)
    assert result["profile"] == "free"
    assert result["use_profile_models"] is True
    assert result["models"] == list(FREE_MODELS)
    assert result["chairman"] == FREE_CHAIRMAN
    assert result["resolved_chairman"] == FREE_CHAIRMAN
    assert result["options"]["profiles"]["free"]["models"] == list(FREE_MODELS)

    import yaml
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["tiers"]["default"] == NATIVE_FREE_TIER
    assert saved["council"]["models"] == list(FREE_MODELS)
    assert saved["council"]["chairman"] == FREE_CHAIRMAN
    assert saved["council"]["style_normalization"] is False
    assert saved["gateways"]["default"] == "openrouter"
    assert saved["gateways"]["fallback"]["enabled"] is False
    assert get_config().council.models == list(FREE_MODELS)


@pytest.mark.parametrize("setting", [
    {"use_profile_models": False},
    {"chairman": "openai/gpt-4o"},
    {"style_normalization": "auto"},
    {"rubric_enabled": True},
])
def test_free_rejects_paid_or_uncontrolled_settings(free_client, setting):
    client, _path = free_client
    payload = {"profile": "free", "style_normalization": "off", **setting}
    r = client.put("/api/settings", json=payload)
    assert r.status_code == 422


def test_free_requires_key_and_does_not_start_models(free_client, monkeypatch):
    client, _path = free_client
    _select_free(client)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("No paid council should be started before key setup")
    monkeypatch.setattr(main, "run_full_council", forbidden)
    conv = client.post("/api/conversations", json={}).json()
    r = client.post(f"/api/conversations/{conv['id']}/message", json={"content": "Hello"})
    assert r.status_code == 400
    assert "OpenRouter API key" in r.json()["detail"]
    stream = client.post(
        f"/api/conversations/{conv['id']}/message/stream", json={"content": "Hello"}
    )
    assert stream.status_code == 400
    assert not storage.get_conversation(conv["id"])["messages"]


def test_free_first_round_avoids_paid_title_and_uses_native_engine(free_client, monkeypatch):
    client, _ = free_client
    _select_free(client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-free-test")
    queries = []
    async def fake_council(question, **kwargs):
        queries.append((question, kwargs, list(get_config().council.models),
                        get_config().council.chairman))
        return _answer()
    async def forbidden_title(_question):
        raise AssertionError("Paid Gemini title must never be called in Free")
    monkeypatch.setattr(main, "run_full_council", fake_council)
    monkeypatch.setattr(main, "generate_conversation_title", forbidden_title)
    conv = client.post("/api/conversations", json={}).json()
    r = client.post(f"/api/conversations/{conv['id']}/message", json={
        "content": "Does the new Council work?", "verdict_type": "binary",
    })
    assert r.status_code == 200, r.text
    assert len(queries) == 1
    assert queries[0][0] == "Does the new Council work?"
    assert queries[0][2] == list(FREE_MODELS)
    assert queries[0][3] == FREE_CHAIRMAN
    assert storage.get_conversation(conv["id"])["title"].startswith("Does the new")


def test_free_followup_summarization_never_uses_paid_flash(free_client, monkeypatch):
    client, _path = free_client
    _select_free(client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-free-test")
    council_calls, summaries = [], []
    async def fake_council(question, **kwargs):
        council_calls.append(question)
        return _answer("A" * 7000 if len(council_calls) == 1 else "Second answer")
    async def fake_summary(model, messages, **kwargs):
        summaries.append((model, json.loads(messages[1]["content"]), kwargs))
        return {"content": "Free memory: the Council previously agreed on a plan."}
    async def forbidden_title(_question):
        raise AssertionError("Title would use a paid model")
    monkeypatch.setattr(main, "run_full_council", fake_council)
    monkeypatch.setattr(main, "generate_conversation_title", forbidden_title)
    monkeypatch.setattr(conversation_context, "query_model", fake_summary)
    conv = client.post("/api/conversations", json={}).json()
    endpoint = f"/api/conversations/{conv['id']}/message/stream"
    for question in ("First question", "Follow up?"):
        r = client.post(endpoint, json={"content": question})
        assert r.status_code == 200
        events = [json.loads(line[6:]) for line in r.text.splitlines()
                  if line.startswith("data: ")]
        assert any(e["type"] == "complete" for e in events)
    assert summaries[0][0] == FREE_SUMMARY_MODEL
    assert summaries[0][0].endswith(":free")
    assert summaries[0][2]["disable_tools"] is True
    assert council_calls[1].endswith("Follow up?")
    assert "Free memory:" in council_calls[1]
    assert storage.get_conversation(conv["id"])["context_memory"]["model"] == FREE_SUMMARY_MODEL


def test_switching_out_of_free_keeps_native_profiles_compatible(free_client):
    client, _path = free_client
    _select_free(client)
    result = client.put("/api/settings", json={
        "profile": "quick", "use_profile_models": True,
    })
    assert result.status_code == 200, result.text
    assert result.json()["profile"] == "quick"
    assert result.json()["resolved_chairman"] != FREE_CHAIRMAN
    assert result.json()["models"] != list(FREE_MODELS)


def test_free_fails_closed_on_environment_model_override(free_client, monkeypatch):
    client, _path = free_client
    _select_free(client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-free-test")
    monkeypatch.setenv("LLM_COUNCIL_CHAIRMAN", "openai/gpt-4o")
    reload_config()
    conv = client.post("/api/conversations", json={}).json()
    r = client.post(f"/api/conversations/{conv['id']}/message", json={"content": "Hello"})
    assert r.status_code == 409
    assert storage.get_conversation(conv["id"])["messages"] == []
    monkeypatch.delenv("LLM_COUNCIL_CHAIRMAN", raising=False)
