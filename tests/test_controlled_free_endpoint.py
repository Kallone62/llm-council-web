"""P0: a Free model name must never authorize a noncanonical provider URL."""
import asyncio
import json
from collections import Counter

import httpx
import pytest
import yaml
from fastapi import HTTPException
from llm_council import council
from llm_council.gateway.resolver import resolve_endpoint
from llm_council.unified_config import get_config, reload_config

from backend.free_profile import FREE_CHAIRMAN, FREE_MODELS, safe_local_title
from test_controlled_gateway_fallback import KEYS, MODELS, PRIMARY, CHAIRMAN, TITLE_MODEL, gateway_config

QUESTION = "ENG24: Which archive is safe to keep?"
FINAL = "Preserve the verified archive."
TITLE = "Archive Retention Decision"
CUSTOM = "https://gateway.example.invalid/api/v1/chat/completions"
ROUTES = {
    "canonical": (PRIMARY, True),
    "implicit": (None, True),
    "other-host": (CUSTOM, False),
    "lookalike-host": ("https://openrouter.ai.example.invalid/api/v1/chat/completions", False),
    "insecure": ("http://openrouter.ai/api/v1/chat/completions", False),
    "other-path": ("https://openrouter.ai/api/v1/other", False),
    "query-override": (PRIMARY + "?route=paid", False),
}


def configure(path, case, free=True):
    url, _ = ROUTES[case]
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if free:
        document["council"]["models"] = list(FREE_MODELS)
        document["council"]["chairman"] = FREE_CHAIRMAN
    provider = document["gateways"]["providers"]["openrouter"]
    if url is None:
        provider.pop("base_url", None)
    else:
        provider["base_url"] = url
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    reload_config()
    assert resolve_endpoint()[0] == (url or PRIMARY)


def install_provider(monkeypatch, free=True):
    calls = []
    models = FREE_MODELS if free else MODELS
    chairman = FREE_CHAIRMAN if free else CHAIRMAN
    expected_url = PRIMARY if free else CUSTOM
    answers = {m: f"Archive candidate {i}." for i, m in enumerate(models)}
    labels = [f"Response {chr(65+i)}" for i in range(len(models))]
    review = json.dumps({"ranking": labels, "scores": dict(zip(labels, range(9, 9-len(models), -1)))})

    async def transport(_transport, request):
        body = json.loads(request.content)
        url, model = str(request.url), body["model"]
        prompt = body["messages"][-1]["content"]
        if url != expected_url:
            # Record the actual unsafe attempted request, then fail at the HTTP
            # boundary. No provider or motor function is mocked.
            calls.append(("unexpected-route", model, url))
            return httpx.Response(503, json={"error": {"message": "fixture route rejected"}})
        assert request.method == "POST"
        assert request.headers["authorization"] == f"Bearer {KEYS[PRIMARY]}"
        if prompt == QUESTION:
            stage, answer = "stage1", answers[model]
        elif "<responses_to_evaluate>" in prompt:
            stage, answer = "stage2", review
            assert model in models and QUESTION in prompt
            assert all(text in prompt for text in answers.values())
        elif prompt.startswith("Generate a very short title"):
            stage, answer = "title", TITLE
            assert not free and model == TITLE_MODEL
        else:
            stage, answer = "stage3", FINAL
            assert prompt.startswith("You are the Chairman of an LLM Council.")
            assert model == chairman and QUESTION in prompt
            assert all(text in prompt for text in answers.values())
        calls.append((stage, model, url))
        return httpx.Response(200, json={"choices": [{"message": {"content": answer}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0}})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transport)
    return calls, answers


def verify_round(result, observed, free=True, title=False):
    calls, answers = observed
    models = FREE_MODELS if free else MODELS
    chairman = FREE_CHAIRMAN if free else CHAIRMAN
    url = PRIMARY if free else CUSTOM
    expected = [(s, m, url) for s in ("stage1", "stage2") for m in models] + [("stage3", chairman, url)]
    if title and not free:
        expected.append(("title", TITLE_MODEL, url))
    assert Counter(calls) == Counter(expected)
    assert [s for s, _, _ in calls if s != "title"] == ["stage1"]*len(models) + ["stage2"]*len(models) + ["stage3"]
    s1, s2, s3, metadata = result
    assert {r["model"]: r["response"] for r in s1} == answers
    assert {r["model"] for r in s2} == set(models)
    assert s3 == {"model": chairman, "response": FINAL}
    assert all(r["vote_count"] == len(models)-1 for r in metadata["aggregate_rankings"])
    assert metadata["usage"]["total"]["total_tokens"] == (2*len(models)+1)*18
    assert metadata.get("status") not in ("failed", "partial")


@pytest.mark.parametrize("case", ROUTES)
def test_native_free_endpoint(gateway_config, monkeypatch, case):
    from backend import main
    configure(gateway_config, case)
    observed = install_provider(monkeypatch)
    result, error = None, None
    try:
        assert main._free_run_guard() is True
        result = asyncio.run(council.run_full_council(QUESTION, bypass_cache=True))
    except HTTPException as exc:
        error = exc
    if ROUTES[case][1]:
        assert error is None
        verify_round(result, observed)
    else:
        assert not observed[0], "Free round reached an unapproved provider endpoint"
        assert error is not None and error.status_code == 409
        assert "conflicting engine or environment override" in error.detail
        assert result is None


def test_native_paid_custom_endpoint_unchanged(gateway_config, monkeypatch):
    from backend import main
    configure(gateway_config, "other-host", free=False)
    observed = install_provider(monkeypatch, free=False)
    assert main._free_run_guard() is False
    verify_round(asyncio.run(council.run_full_council(QUESTION, bypass_cache=True)), observed, free=False)


def decode_response(response, stream, free=True):
    assert response.status_code == 200, response.text
    if not stream:
        return response.json()
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    expected = ["engine_progress"]*8 + ["stage1_complete", "stage2_complete", "stage3_complete", "complete"]
    if not free:
        expected.append("title_complete")
        assert events[-1]["data"] == {"title": TITLE}
    assert [e["type"] for e in events] == expected
    progress = [e["data"] for e in events[:8]]
    assert [(e["stage"], e["state"]) for e in progress] == [
        (s, state) for s in ("stage1", "stage1_5", "stage2", "stage3") for state in ("started", "completed")]
    terminal = {e["type"]: e for e in events[8:]}
    metadata = terminal["complete"]["metadata"]
    assert terminal["stage2_complete"]["metadata"] == terminal["stage3_complete"]["metadata"] == metadata
    assert metadata["ui_runtime_progress"] == progress
    return {**{s: terminal[f"{s}_complete"]["data"] for s in ("stage1", "stage2", "stage3")}, "metadata": metadata}


@pytest.mark.parametrize("case", ROUTES)
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_free_endpoint_api(gateway_config, monkeypatch, tmp_path, case, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    configure(gateway_config, case)
    observed = install_provider(monkeypatch)
    with TestClient(main.app) as client:
        selected = client.put("/api/settings", json={"profile": "free"})
        assert selected.status_code == 200 and selected.json()["profile"] == "free"
        assert resolve_endpoint()[0] == (ROUTES[case][0] or PRIMARY)
        assert get_config().gateways.fallback.enabled is False
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        before = storage.get_conversation(cid)
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        response = client.post(endpoint, json={"content": QUESTION})
        if not ROUTES[case][1]:
            assert not observed[0], "Free API reached an unapproved provider endpoint"
            assert response.status_code == 409
            assert "conflicting engine or environment override" in response.json()["detail"]
            assert storage.get_conversation(cid) == before
            assert client.get(f"/api/conversations/{cid}").json() == before
            # Rejected rounds must not leak an active-run reservation. Restore
            # the route and run the same conversation without losing data.
            configure(gateway_config, "canonical")
            selected = client.put("/api/settings", json={"profile": "free"})
            assert selected.status_code == 200
            response = client.post(endpoint, json={"content": QUESTION})
        result = decode_response(response, stream)
        verify_round(tuple(result[k] for k in ("stage1", "stage2", "stage3", "metadata")), observed, title=True)
        assert result["metadata"]["peer_review"]["state"] == "completed"
        assert result["metadata"]["peer_review"]["effective_votes"] == 6
        disk = storage.get_conversation(cid)
        assert disk["title"] == safe_local_title(QUESTION)
        assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **result}]
        assert client.get(f"/api/conversations/{cid}").json() == disk


@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_paid_custom_endpoint_api_unchanged(gateway_config, monkeypatch, tmp_path, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    configure(gateway_config, "other-host", free=False)
    observed = install_provider(monkeypatch, free=False)
    with TestClient(main.app) as client:
        selected = client.put("/api/settings", json={"profile": "high"})
        assert selected.status_code == 200 and selected.json()["profile"] == "high"
        assert resolve_endpoint()[0] == CUSTOM
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = decode_response(client.post(endpoint, json={"content": QUESTION}), stream, free=False)
        verify_round(tuple(result[k] for k in ("stage1", "stage2", "stage3", "metadata")), observed, free=False, title=True)
        disk = storage.get_conversation(cid)
        assert disk["title"] == TITLE
        assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **result}]
        assert client.get(f"/api/conversations/{cid}").json() == disk
