"""ENG-10: native HTTP-boundary acceptance of rolling-memory failure/recovery."""
import asyncio
from collections import Counter
from copy import deepcopy
import json
import os
import socket

import httpx
import pytest
import yaml

from backend.conversation_context import ContextSummaryError, prepare_council_query
from backend.free_profile import FREE_CHAIRMAN, FREE_MODELS, FREE_SUMMARY_MODEL
from llm_council.unified_config import reload_config

OLD_MEMORY = {"version": 3, "summary": "Keep the original BLUE archive.",
              "covered_turns": 1, "model": FREE_SUMMARY_MODEL}
LONG_QUESTION = "Pending requirement: " + "Retain the original record. " * 240
LONG_ANSWER = "Confirmed: preserve the entire original record."
CURRENT = "ENG-10: Which archive is retained?"
SUMMARY = "Keep the original BLUE archive and preserve the entire original record."
FINAL = "Retain the original BLUE archive."
ANSWERS = {model: f"Candidate {i}: retain BLUE archive." for i, model in enumerate(FREE_MODELS)}
LABELS = ["Response A", "Response B", "Response C"]
REVIEW = "Review of the candidates.\n```json\n" + json.dumps({"ranking": LABELS, "scores": dict(zip(LABELS, [9, 8, 7]))}) + "\n```"
EXPECTED_SUMMARY_INPUT = {"previous_memory": OLD_MEMORY["summary"],
                          "completed_exchanges_since_memory": [{"user_question": LONG_QUESTION, "chairman_final_answer": LONG_ANSWER}]}
EXPECTED_QUERY = ("Conversation background from prior completed Council rounds (not the current request):\n\n"
                  "Earlier conversation memory (AI-generated summary; consult the actual current question if there is any conflict):\n"
                  + SUMMARY + "\n\nCurrent user question for this Council round:\n" + CURRENT)
FAILURES = ("503", "429", "timeout", "empty", "whitespace", "null", "oversized")


def seeded_history():
    return {"messages": [
        {"role": "user", "content": "Which original archive?"},
        {"role": "assistant", "stage1": [], "stage2": [], "stage3": {"model": FREE_CHAIRMAN, "response": "Keep BLUE."}},
        {"role": "user", "content": "FAILED_RED_QUESTION"},
        {"role": "assistant", "stage1": [], "stage2": [], "stage3": {"model": FREE_CHAIRMAN, "response": "FAILED_RED_RESPONSE", "error_status": "error"}},
        {"role": "user", "content": LONG_QUESTION},
        {"role": "assistant", "stage1": [], "stage2": [], "stage3": {"model": FREE_CHAIRMAN, "response": LONG_ANSWER}},
    ], "context_memory": deepcopy(OLD_MEMORY)}


@pytest.fixture
def memory_http(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith(("LLM_COUNCIL_", "OTEL_", "POSTHOG_")) or name.lower().endswith("_proxy"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "eng10-test-not-a-real-key")
    monkeypatch.setenv("LLM_COUNCIL_PERFORMANCE_TRACKING", "false")
    monkeypatch.setenv("LLM_COUNCIL_EARLY_CONSENSUS", "false")
    monkeypatch.setenv("LLM_COUNCIL_PERFORMANCE_STORE", str(tmp_path / "performance.jsonl"))
    config = tmp_path / "eng10.yaml"
    config.write_text(yaml.safe_dump({
        "tiers": {"default": "high"},
        "council": {"models": list(FREE_MODELS), "chairman": FREE_CHAIRMAN,
                    "synthesis_mode": "consensus", "exclude_self_votes": True, "style_normalization": False},
        "gateways": {"default": "openrouter", "fallback": {"enabled": False}},
        "evaluation": {"rubric": {"enabled": False}, "safety": {"enabled": False},
                       "bias": {"audit_enabled": False, "persistence_enabled": False}},
        "cache": {"enabled": False, "ttl_seconds": 0},
    }), encoding="utf-8")
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(config))
    reload_config()
    state = {"failure": None, "calls": []}

    def reply(request):
        assert request.method == "POST" and str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers.get("authorization") == "Bearer eng10-test-not-a-real-key"
        body = json.loads(request.content)
        model = body["model"]
        assert model in FREE_MODELS
        messages = body["messages"]
        prompt = messages[-1]["content"]
        if messages[0]["role"] == "system" and "You maintain concise, faithful memory" in messages[0]["content"]:
            stage = "memory"
            assert model == FREE_SUMMARY_MODEL
            assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 45.0)
            assert json.loads(prompt) == EXPECTED_SUMMARY_INPUT
            state["calls"].append((stage, model))
            failure = state["failure"]
            if failure in ("503", "429"):
                return httpx.Response(int(failure), json={"error": {"code": int(failure), "message": "ENG10 summary unavailable"}}, headers={"Retry-After": "120"} if failure == "429" else {})
            if failure == "timeout":
                raise httpx.ReadTimeout("Controlled summary timeout", request=request)
            content = {"empty": "", "whitespace": " \n\t", "null": None, "oversized": "x" * 4001}.get(failure, SUMMARY)
        else:
            assert state["failure"] is None, "Council must not run without the required memory"
            assert EXPECTED_QUERY in prompt
            assert "FAILED_RED" not in prompt
            if "<responses_to_evaluate>" in prompt:
                stage, content = "stage2", REVIEW
                assert all(answer in prompt for answer in ANSWERS.values())
            elif prompt.startswith("You are the Chairman"):
                stage, content = "stage3", FINAL
                assert model == FREE_CHAIRMAN and "AGGREGATE RANKINGS" in prompt and REVIEW in prompt
            else:
                stage, content = "stage1", ANSWERS[model]
                assert prompt == EXPECTED_QUERY
            state["calls"].append((stage, model))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
                                        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0}})

    transport = httpx.MockTransport(reply)

    async def handle(_self, request):
        return await transport.handle_async_request(request)

    def deny_network(*args, **kwargs):
        pytest.fail("Unexpected real network during controlled memory acceptance")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    original_connect = socket.socket.connect

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            deny_network()
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    yield state


@pytest.mark.parametrize("failure", FAILURES)
def test_eng10_native_summary_failure_and_retry(memory_http, failure):
    history = seeded_history()
    original = deepcopy(history)
    progress = []

    async def report(state):
        progress.append(state)

    async def drive():
        memory_http["failure"] = failure
        with pytest.raises(ContextSummaryError):
            await prepare_council_query(history, CURRENT, progress_callback=report, summary_model=FREE_SUMMARY_MODEL)
        assert progress == ["started", "failed"]
        assert history == original
        assert memory_http["calls"] == [("memory", FREE_SUMMARY_MODEL)]
        memory_http["failure"] = None
        query, memory = await prepare_council_query(history, CURRENT, progress_callback=report, summary_model=FREE_SUMMARY_MODEL)
        assert query == EXPECTED_QUERY
        assert memory == {"version": 3, "summary": SUMMARY, "covered_turns": 2, "model": FREE_SUMMARY_MODEL}
        assert history == original
        assert progress == ["started", "failed", "started", "completed"]
        assert memory_http["calls"] == [("memory", FREE_SUMMARY_MODEL)] * 2
        history["context_memory"] = memory
        cached_query, new_memory = await prepare_council_query(history, CURRENT, progress_callback=report, summary_model=FREE_SUMMARY_MODEL)
        assert cached_query == EXPECTED_QUERY and new_memory is None
        assert memory_http["calls"] == [("memory", FREE_SUMMARY_MODEL)] * 2

    asyncio.run(drive())


@pytest.mark.parametrize("failure", FAILURES)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_eng10_api_summary_failure_and_retry(memory_http, tmp_path, monkeypatch, failure, stream):
    from fastapi.testclient import TestClient
    from backend import main, storage
    from llm_council import council

    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        original = {**storage.get_conversation(cid), **seeded_history()}
        storage.save_conversation(original)
        path = tmp_path / "conversations" / f"{cid}.json"
        raw = path.read_bytes()
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        memory_http["failure"] = failure
        response = client.post(endpoint, json={"content": CURRENT})
        message = "Could not prepare AI conversation memory. Please retry."
        if stream:
            assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert [event["type"] for event in events] == ["engine_progress", "engine_progress", "error"]
            assert [(event["data"]["stage"], event["data"]["state"]) for event in events[:2]] == [("memory", "started"), ("memory", "failed")]
            assert events[-1]["message"] == message
        else:
            assert response.status_code == 503 and response.json() == {"detail": message}
        assert memory_http["calls"] == [("memory", FREE_SUMMARY_MODEL)]
        assert path.read_bytes() == raw
        assert storage.get_conversation(cid) == original
        assert client.get(f"/api/conversations/{cid}").json()["messages"] == original["messages"]

        memory_http["failure"] = None
        response = client.post(endpoint, json={"content": CURRENT})
        assert response.status_code == 200, response.text
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert [e["type"] for e in events] == ["engine_progress"] * 10 + ["stage1_complete", "stage2_complete", "stage3_complete", "complete"]
            progress = [e["data"] for e in events[:10]]
            assert [(e["stage"], e["state"]) for e in progress[:2]] == [("memory", "started"), ("memory", "completed")]
            stage1, stage2, stage3 = [e["data"] for e in events[-4:-1]]
            metadata = events[-1]["metadata"]
            assert events[-3]["metadata"] == events[-2]["metadata"] == metadata
            assert metadata["ui_runtime_progress"] == progress
        else:
            stage1, stage2, stage3, metadata = [response.json()[key] for key in ("stage1", "stage2", "stage3", "metadata")]
        expected = [("memory", FREE_SUMMARY_MODEL)] * 2 + [(stage, model) for stage in ("stage1", "stage2") for model in FREE_MODELS] + [("stage3", FREE_CHAIRMAN)]
        assert Counter(memory_http["calls"]) == Counter(expected)
        assert [stage for stage, _ in memory_http["calls"]] == [stage for stage, _ in expected]
        assert {r["model"]: r["response"] for r in stage1} == ANSWERS
        assert len(stage2) == 3 and all(r["parsed_ranking"]["ranking"] == LABELS for r in stage2)
        assert stage3["model"] == FREE_CHAIRMAN and stage3["response"] == FINAL and not stage3.get("error_status")
        assert metadata["peer_review"]["state"] == "completed" and metadata["peer_review"]["effective_votes"] == 6
        assert metadata.get("status") not in ("failed", "partial")
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved == {**original, "context_memory": {"version": 3, "summary": SUMMARY, "covered_turns": 2, "model": FREE_SUMMARY_MODEL},
                         "messages": original["messages"] + [{"role": "user", "content": CURRENT}, {"role": "assistant", "stage1": stage1, "stage2": stage2, "stage3": stage3, "metadata": metadata}]}
        assert client.get(f"/api/conversations/{cid}").json()["messages"] == saved["messages"]
