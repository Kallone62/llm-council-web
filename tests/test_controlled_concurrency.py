"""ENG-12: concurrent native rounds, isolated provider traffic and cancellation."""
import asyncio
from collections import Counter
from copy import deepcopy
import json
import os
import socket

import httpx
import pytest
import yaml

from backend.free_profile import FREE_CHAIRMAN, FREE_MODELS, FREE_SUMMARY_MODEL
from llm_council import council
from llm_council.unified_config import get_config, reload_config

ALPHA, BRAVO = "ENG12_ALPHA", "ENG12_BRAVO"
TAGS = (ALPHA, BRAVO)
KEY = "eng12-test-only-not-a-real-key"
ALT1, ALT2 = FREE_MODELS[1:]


def question(tag):
    return f"{tag}: Which archive must be retained now?"


def old_memory(tag):
    return {"version": 3, "summary": f"{tag}: retain the initial archive.",
            "covered_turns": 1, "model": FREE_SUMMARY_MODEL}


def long_question(tag):
    return f"{tag}: " + "Preserve each original record without alteration. " * 150


def long_answer(tag):
    return f"{tag}: preserve the original record."


def summary(tag):
    return f"{tag}: retain the initial archive and preserve each original record."


def effective_query(tag):
    return ("Conversation background from prior completed Council rounds (not the current request):\n\n"
            "Earlier conversation memory (AI-generated summary; consult the actual current question if there is any conflict):\n"
            + summary(tag) + "\n\nCurrent user question for this Council round:\n" + question(tag))


def answer(tag, model):
    return f"{tag}: candidate {FREE_MODELS.index(model) + 1} retains the original archive."


def final(tag):
    return f"{tag}: retain the archive and preserve every record."


def expected_models(tag):
    return list(FREE_MODELS[1:] if tag == ALPHA else FREE_MODELS)


def review(tag):
    labels = [f"Response {letter}" for letter in "ABC"[:len(expected_models(tag))]]
    return f"{tag}: review of the archive candidates.\n```json\n" + json.dumps({
        "ranking": labels, "scores": dict(zip(labels, (9, 8, 7))),
    }) + "\n```"


def usage(tag):
    prompt = 11 if tag == ALPHA else 23
    return {"prompt_tokens": prompt, "completion_tokens": 7,
            "total_tokens": prompt + 7, "cost": 0.0}


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    from backend import config, storage

    for name in tuple(os.environ):
        if name.startswith(("LLM_COUNCIL_", "OTEL_", "POSTHOG_")) or name.lower().endswith("_proxy"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setenv("LLM_COUNCIL_PERFORMANCE_TRACKING", "false")
    monkeypatch.setenv("LLM_COUNCIL_EARLY_CONSENSUS", "false")
    monkeypatch.setenv("LLM_COUNCIL_PERFORMANCE_STORE", str(tmp_path / "performance.jsonl"))
    path = tmp_path / "eng12.yaml"
    path.write_text(yaml.safe_dump({
        "tiers": {"default": "high"},
        "council": {"models": list(FREE_MODELS), "chairman": FREE_CHAIRMAN,
                    "synthesis_mode": "consensus", "exclude_self_votes": True,
                    "style_normalization": False},
        "gateways": {"default": "openrouter", "fallback": {"enabled": False}},
        "evaluation": {"rubric": {"enabled": False}, "safety": {"enabled": False},
                       "bias": {"audit_enabled": False, "persistence_enabled": False}},
        "cache": {"enabled": False, "ttl_seconds": 0},
        "timeouts": {"multiplier": 2.0},
    }), encoding="utf-8")
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(path))
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    reload_config()
    return path


class ProviderBoundary:
    """Only provider HTTP is replaced. Events establish overlap, not sleeps."""
    def __init__(self, monkeypatch):
        self.calls = {tag: [] for tag in TAGS}
        self.cancelled = {tag: [] for tag in TAGS}
        self.stage1_ready = {tag: asyncio.Event() for tag in TAGS}
        self.stage1_release = {tag: asyncio.Event() for tag in TAGS}
        self.alt_ready = asyncio.Event()
        self.alt_release = asyncio.Event()

        def deny_network(*args, **kwargs):
            pytest.fail("Unexpected external network during ENG-12")

        monkeypatch.setattr(socket, "getaddrinfo", deny_network)
        original_connect = socket.socket.connect

        def connect(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                deny_network()
            return original_connect(sock, address)

        monkeypatch.setattr(socket.socket, "connect", connect)

        async def handle(_transport, request):
            return await self.reply(request)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)

    async def reply(self, request):
        assert request.method == "POST"
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers.get("authorization") == f"Bearer {KEY}"
        body = json.loads(request.content)
        model, messages = body["model"], body["messages"]
        assert model in FREE_MODELS
        prompt = messages[-1]["content"]
        found = [tag for tag in TAGS if tag in prompt]
        assert len(found) == 1, "Conversation identity missing or mixed in HTTP prompt"
        tag = found[0]
        other = next(value for value in TAGS if value != tag)
        assert other not in json.dumps(messages)
        status = 200
        if messages[0]["role"] == "system" and "You maintain concise, faithful memory" in messages[0]["content"]:
            stage, content, timeout = "memory", summary(tag), 45.0
            assert model == FREE_SUMMARY_MODEL
            assert json.loads(prompt) == {"previous_memory": old_memory(tag)["summary"],
                "completed_exchanges_since_memory": [{"user_question": long_question(tag),
                                                       "chairman_final_answer": long_answer(tag)}]}
        elif prompt == effective_query(tag):
            stage, content, timeout = "stage1", answer(tag, model), 120.0
            if tag == ALPHA and model == FREE_CHAIRMAN:
                status = 429
        elif "<responses_to_evaluate>" in prompt:
            stage, content, timeout = "stage2", review(tag), 180.0
            assert model in expected_models(tag)
            assert effective_query(tag) in prompt
            assert all(answer(tag, m) in prompt for m in expected_models(tag))
            if tag == ALPHA:
                assert answer(tag, FREE_CHAIRMAN) not in prompt
        elif prompt.startswith("You are the Chairman of an LLM Council."):
            stage, content, timeout = "stage3", final(tag), 180.0
            assert effective_query(tag) in prompt
            assert all(answer(tag, m) in prompt for m in expected_models(tag))
            assert review(tag) in prompt and "AGGREGATE RANKINGS" in prompt
            if tag == ALPHA:
                assert model in (ALT1, ALT2)
                if model == ALT1:
                    status = 503
            else:
                assert model == FREE_CHAIRMAN, "Another round's Chairman override leaked"
        else:
            pytest.fail("Unexpected provider prompt during ENG-12")
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), timeout)
        self.calls[tag].append((stage, model, status))
        try:
            if stage == "stage1":
                if sum(entry[0] == stage for entry in self.calls[tag]) == 3:
                    self.stage1_ready[tag].set()
                await self.stage1_release[tag].wait()
            if stage == "stage3" and tag == ALPHA and model == ALT1:
                self.alt_ready.set()
                await self.alt_release.wait()
        except asyncio.CancelledError:
            self.cancelled[tag].append((stage, model))
            raise
        if status != 200:
            return httpx.Response(status, headers={"Retry-After": "120"} if status == 429 else {},
                                  json={"error": {"code": status, "message": "Controlled provider error"}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
                                         "usage": usage(tag)})

    def release_all(self):
        for event in self.stage1_release.values():
            event.set()
        self.alt_release.set()


def expected_calls(tag):
    calls = [("memory", FREE_SUMMARY_MODEL, 200)]
    calls += [("stage1", model, 429 if tag == ALPHA and model == FREE_CHAIRMAN else 200)
              for model in FREE_MODELS]
    calls += [("stage2", model, 200) for model in expected_models(tag)]
    calls += [("stage3", ALT1, 503), ("stage3", ALT2, 200)] if tag == ALPHA else [("stage3", FREE_CHAIRMAN, 200)]
    return calls


async def seed(client, tag):
    from backend import storage

    response = await client.post("/api/conversations", json={})
    assert response.status_code == 200
    cid = response.json()["id"]
    conversation = storage.get_conversation(cid)
    conversation["title"] = f"{tag} archive"
    conversation["messages"] = [
        {"role": "user", "content": f"{tag}: original question"},
        {"role": "assistant", "stage1": [], "stage2": [],
         "stage3": {"model": FREE_CHAIRMAN, "response": f"{tag}: initial archive"}},
        {"role": "user", "content": long_question(tag)},
        {"role": "assistant", "stage1": [], "stage2": [],
         "stage3": {"model": FREE_CHAIRMAN, "response": long_answer(tag)}},
    ]
    conversation["context_memory"] = old_memory(tag)
    storage.save_conversation(conversation)
    return cid, deepcopy(conversation)


async def assert_settings(client, config, *, active):
    before = config.read_bytes()
    result = await client.put("/api/settings", json={"profile": "free", "timeout_multiplier": 1.0})
    assert result.status_code == (409 if active else 200), result.text
    current = (await client.get("/api/settings")).json()
    assert current["profile"] == "free" and current["models"] == list(FREE_MODELS)
    assert current["chairman"] == FREE_CHAIRMAN
    assert current["timeout_multiplier"] == (2.0 if active else 1.0)
    assert not get_config().gateways.fallback.enabled
    if active:
        assert config.read_bytes() == before


def parse_result(response, stream, tag):
    assert response.status_code == 200, response.text
    if not stream:
        return response.json()
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [e["type"] for e in events[-4:]] == ["stage1_complete", "stage2_complete", "stage3_complete", "complete"]
    assert all(e["type"] == "engine_progress" for e in events[:-4])
    progress = [e["data"] for e in events[:-4]]
    transitions = [(stage, state) for stage in ("memory", "stage1", "stage1_5", "stage2", "stage3")
                   for state in ("started", "completed")]
    if tag == ALPHA:
        transitions[-1:-1] = [("stage3", "retrying"), ("stage3", "retrying")]
        assert [e["model"] for e in progress if e["state"] == "retrying"] == [ALT1, ALT2]
        assert progress[3]["rate_limits"] == {FREE_CHAIRMAN: 120}
    else:
        assert "rate_limits" not in progress[3]
    assert [(e["stage"], e["state"]) for e in progress] == transitions
    assert progress[3]["responses"] == len(expected_models(tag))
    assert progress[7]["reviews"] == len(expected_models(tag))
    assert progress[7]["valid_reviews"] == len(expected_models(tag))
    assert all(e["elapsed_seconds"] >= 0 for e in progress)
    metadata = events[-1]["metadata"]
    assert events[-3]["metadata"] == events[-2]["metadata"] == metadata
    assert metadata["ui_runtime_progress"] == progress
    return {"stage1": events[-4]["data"], "stage2": events[-3]["data"],
            "stage3": events[-2]["data"], "metadata": metadata}


async def assert_record(client, cid, original, tag, payload):
    from backend import storage

    expected = deepcopy(original)
    expected["context_memory"] = {"version": 3, "summary": summary(tag),
                                  "covered_turns": 2, "model": FREE_SUMMARY_MODEL}
    expected["messages"].append({"role": "user", "content": question(tag)})
    if payload is not None:
        expected["messages"].append({"role": "assistant", **payload})
    disk = storage.get_conversation(cid)
    assert disk == expected
    result = await client.get(f"/api/conversations/{cid}")
    assert result.status_code == 200
    assert result.json() == {k: v for k, v in expected.items() if k != "context_memory"}
    other = next(value for value in TAGS if value != tag)
    assert other not in json.dumps(disk)


def assert_round(payload, boundary, tag):
    models = expected_models(tag)
    assert Counter(boundary.calls[tag]) == Counter(expected_calls(tag))
    assert [stage for stage, _, _ in boundary.calls[tag]] == [stage for stage, _, _ in expected_calls(tag)]
    assert not boundary.cancelled[tag]
    assert {r["model"]: r["response"] for r in payload["stage1"]} == {m: answer(tag, m) for m in models}
    assert {r["model"] for r in payload["stage2"]} == set(models)
    assert len(payload["stage2"]) == len(models)
    assert all(r["ranking"] == review(tag) for r in payload["stage2"])
    stage3 = payload["stage3"]
    assert stage3["model"] == (ALT2 if tag == ALPHA else FREE_CHAIRMAN)
    assert stage3["response"] == final(tag) and not stage3.get("error_status")
    metadata = payload["metadata"]
    assert {v["model"] for v in metadata["label_to_model"].values()} == set(models)
    assert {r["model"] for r in metadata["aggregate_rankings"]} == set(models)
    assert all(r["vote_count"] == len(models) - 1 for r in metadata["aggregate_rankings"])
    assert metadata["peer_review"]["effective_votes"] == len(models) * (len(models) - 1)
    assert metadata["peer_review"]["state"] == "completed"
    assert metadata["config"]["council_size"] == 3
    assert metadata["config"]["responses_received"] == len(models)
    assert metadata["config"]["chairman"] == FREE_CHAIRMAN
    assert metadata["config"]["exclude_self_votes"] is True
    assert metadata["usage"]["total"]["total_tokens"] == (2 * len(models) + 1) * usage(tag)["total_tokens"]
    assert metadata["usage"]["total"]["cost_usd"] == 0
    assert metadata["usage"]["total"]["cost_known"] is True
    if tag == ALPHA:
        assert metadata["stage1_rate_limits"] == {FREE_CHAIRMAN: 120}
        assert metadata["status"] == "partial" and metadata["degraded_mode"] == "two_models"
    else:
        assert "stage1_rate_limits" not in metadata
        assert metadata.get("status") not in ("failed", "partial") and not metadata.get("degraded_mode")


async def exercise(monkeypatch, config, streams, cancel_at=None):
    from backend import main

    assert main.run_full_council is council.run_full_council
    boundary = ProviderBoundary(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        records = {tag: await seed(client, tag) for tag in TAGS}
        tasks = {}
        try:
            for tag, stream in zip(TAGS, streams):
                cid, _ = records[tag]
                path = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
                tasks[tag] = asyncio.create_task(client.post(path, json={"content": question(tag)}))
                await asyncio.wait_for(boundary.stage1_ready[tag].wait(), 10)
            assert all(not task.done() for task in tasks.values())
            # Both requests have reached all three real Stage 1 HTTP calls.
            await assert_settings(client, config, active=True)
            if cancel_at != "stage1":
                boundary.stage1_release[ALPHA].set()
                await asyncio.wait_for(boundary.alt_ready.wait(), 10)
            if cancel_at:
                tasks[ALPHA].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(tasks[ALPHA], 10)
                cancelled = ([("stage1", model) for model in FREE_MODELS] if cancel_at == "stage1" else [("stage3", ALT1)])
                assert Counter(boundary.cancelled[ALPHA]) == Counter(cancelled)
                expected = expected_calls(ALPHA)[:4 if cancel_at == "stage1" else -1]
                assert Counter(boundary.calls[ALPHA]) == Counter(expected)
                await assert_record(client, *records[ALPHA], ALPHA, None)
                assert not tasks[BRAVO].done()
                await assert_settings(client, config, active=True)
            # BRAVO runs while ALPHA's alternate Chairman is still in flight,
            # or after ALPHA was cancelled. It must not inherit ALPHA's state.
            boundary.stage1_release[BRAVO].set()
            bravo = parse_result(await asyncio.wait_for(tasks[BRAVO], 20), streams[1], BRAVO)
            assert_round(bravo, boundary, BRAVO)
            await assert_record(client, *records[BRAVO], BRAVO, bravo)
            if not cancel_at:
                assert not tasks[ALPHA].done()
                await assert_settings(client, config, active=True)
                boundary.alt_release.set()
                alpha = parse_result(await asyncio.wait_for(tasks[ALPHA], 20), streams[0], ALPHA)
                assert_round(alpha, boundary, ALPHA)
                await assert_record(client, *records[ALPHA], ALPHA, alpha)
            else:
                # Cancellation did not create a delayed assistant result.
                await assert_record(client, *records[ALPHA], ALPHA, None)
                assert Counter(boundary.calls[ALPHA]) == Counter(expected)
            await assert_settings(client, config, active=False)
            assert council._get_chairman_model() == FREE_CHAIRMAN
        finally:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            boundary.release_all()
            await asyncio.gather(*tasks.values(), return_exceptions=True)


@pytest.mark.parametrize("streams", [(False, False), (False, True), (True, False), (True, True)],
                         ids=["rest-rest", "rest-sse", "sse-rest", "sse-sse"])
def test_eng12_concurrent_rounds(isolated_config, monkeypatch, streams):
    asyncio.run(exercise(monkeypatch, isolated_config, streams))


@pytest.mark.parametrize("streams", [(False, True), (True, False)], ids=["cancel-rest", "cancel-sse"])
@pytest.mark.parametrize("cancel_at", ["stage1", "stage3"])
def test_eng12_cancelled_round_isolated(isolated_config, monkeypatch, streams, cancel_at):
    asyncio.run(exercise(monkeypatch, isolated_config, streams, cancel_at))
