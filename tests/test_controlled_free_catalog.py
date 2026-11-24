"""Free preflight must prove zero catalog prices before any inference call."""
import asyncio
import inspect
import json
from threading import Event
from copy import deepcopy

import httpx
import pytest
from fastapi import HTTPException
from llm_council import council

from backend.free_profile import FREE_CHAIRMAN, FREE_MODELS, safe_local_title
from test_controlled_gateway_fallback import KEYS, PRIMARY, gateway_config
from test_controlled_free_endpoint import (
    QUESTION, TITLE, configure, decode_response, install_provider, verify_round,
)

CATALOG = "https://openrouter.ai/api/v1/models"
PRICING = {"prompt": "0", "completion": "0.0", "request": "0", "image": "0",
           "input_cache_read": "0", "input_cache_write": "0", "internal_reasoning": "0"}
CASES = (
    "zero", "numeric-zero", "missing-model", "duplicate-model", "paid-prompt",
    "paid-completion", "paid-request", "paid-extra", "missing-prompt",
    "missing-request", "null-pricing", "null-price", "bool-price", "malformed-price",
    "nan-price", "tiny-price", "tiny-numeric", "negative-price", "duplicate-price-key",
    "empty-data", "malformed-json",
    "http-401", "http-429", "http-503", "redirect", "timeout",
)
VALID = {"zero", "numeric-zero"}


def catalog_payload(case):
    data = [{"id": m, "pricing": deepcopy(PRICING)} for m in FREE_MODELS]
    data.append({"id": "fixture/paid", "pricing": {"prompt": "1", "completion": "1", "request": "1"}})
    # Paid entries unrelated to the selected models must not block Free.
    if case == "missing-model":
        data.pop(1)
    elif case == "duplicate-model":
        data.append(deepcopy(data[1]))
    elif case.startswith("paid-"):
        data[1]["pricing"][{"paid-prompt": "prompt", "paid-completion": "completion",
                           "paid-request": "request", "paid-extra": "internal_reasoning"}[case]] = "0.00001"
    elif case.startswith("missing-"):
        del data[1]["pricing"][case.removeprefix("missing-")]
    elif case == "null-pricing":
        data[1]["pricing"] = None
    elif case == "numeric-zero":
        data[1]["pricing"] = {key: 0 for key in PRICING}
    elif case == "empty-data":
        data = []
    elif case in {"null-price", "bool-price", "malformed-price", "nan-price", "tiny-price", "negative-price"}:
        data[1]["pricing"]["completion"] = {
            "null-price": None, "bool-price": False, "malformed-price": "0_0",
            "nan-price": "NaN", "tiny-price": "1e-9999", "negative-price": "-0.01",
        }[case]
    return {"data": data}


def install_catalog(monkeypatch, case, trace):
    def handle(_transport, request):
        trace.append((request.method, str(request.url)))
        assert request.method == "GET" and str(request.url) == CATALOG
        assert request.headers.get("authorization") == f"Bearer {KEYS[PRIMARY]}"
        assert not request.content
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 10.0)
        if case == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if case.startswith("http-"):
            return httpx.Response(int(case.split("-")[1]), json={"error": "fixture"})
        if case == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.invalid/models"})
        if case == "malformed-json":
            return httpx.Response(200, content=b"not JSON")
        if case == "tiny-numeric":
            payload = catalog_payload("tiny-price")
            return httpx.Response(200, content=json.dumps(payload).replace('"1e-9999"', '1e-9999'))
        if case == "duplicate-price-key":
            # JSON must not hide a nonzero price by overwriting a duplicate key.
            payload = json.dumps(catalog_payload("zero")).replace('"prompt": "0"', '"prompt": "1", "prompt": "0"', 1)
            return httpx.Response(200, content=payload)
        return httpx.Response(200, json=catalog_payload(case))

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)


def observe(monkeypatch, case, free=True):
    trace = []
    observed = install_provider(monkeypatch, free=free)
    original = httpx.AsyncHTTPTransport.handle_async_request

    async def handle(transport, request):
        trace.append((request.method, str(request.url)))
        return await original(transport, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    install_catalog(monkeypatch, case, trace)
    return trace, observed


async def reserve(main):
    # Support the pre-change synchronous reservation in the baseline run;
    # the oracle concerns ordering and provider calls, not coroutine syntax.
    result = main._reserve_council_run()
    return await result if inspect.isawaitable(result) else result


@pytest.mark.parametrize("case", CASES)
def test_native_catalog_preflight(gateway_config, monkeypatch, case):
    from backend import main
    configure(gateway_config, "canonical")
    trace, observed = observe(monkeypatch, case)

    async def run():
        assert await reserve(main) is True
        try:
            return await council.run_full_council(QUESTION, bypass_cache=True)
        finally:
            main._release_council_run()

    if case in VALID:
        result = asyncio.run(run())
        assert trace == [("GET", CATALOG)] + [("POST", PRIMARY)] * 7
        verify_round(result, observed)
    else:
        with pytest.raises(HTTPException) as err:
            asyncio.run(run())
        assert err.value.status_code == 503
        assert trace == [("GET", CATALOG)] and not observed[0]
        assert main._active_council_runs == 0
        assert main._update_when_idle(lambda: "idle") == "idle"


@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
@pytest.mark.parametrize("case", CASES)
def test_catalog_api(gateway_config, monkeypatch, tmp_path, stream, case):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    configure(gateway_config, "canonical")
    trace, observed = observe(monkeypatch, case)
    with TestClient(main.app) as client:
        selected = client.put("/api/settings", json={"profile": "free"})
        assert selected.status_code == 200
        cid = client.post("/api/conversations", json={}).json()["id"]
        before = storage.get_conversation(cid)
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        response = client.post(endpoint, json={"content": QUESTION})
        if case not in VALID:
            assert trace == [("GET", CATALOG)] and not observed[0]
            assert response.status_code == 503
            assert "Free model catalog" in response.json()["detail"]
            assert storage.get_conversation(cid) == before
            assert client.get(f"/api/conversations/{cid}").json() == before
            assert main._active_council_runs == 0
            # The next round must revalidate, not reuse a negative or stale result.
            trace.clear()
            install_catalog(monkeypatch, "zero", trace)
            assert client.put("/api/settings", json={"profile": "free"}).status_code == 200
            response = client.post(endpoint, json={"content": QUESTION})
        assert trace == [("GET", CATALOG)] + [("POST", PRIMARY)] * 7
        result = decode_response(response, stream)
        verify_round(tuple(result[k] for k in ("stage1", "stage2", "stage3", "metadata")), observed, title=True)
        disk = storage.get_conversation(cid)
        assert disk["title"] == safe_local_title(QUESTION)
        assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **result}]
        assert client.get(f"/api/conversations/{cid}").json() == disk


@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_paid_profile_does_not_query_catalog(gateway_config, monkeypatch, tmp_path, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    configure(gateway_config, "other-host", free=False)
    trace, observed = observe(monkeypatch, "http-503", free=False)
    with TestClient(main.app) as client:
        assert client.put("/api/settings", json={"profile": "high"}).status_code == 200
        cid = client.post("/api/conversations", json={}).json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = decode_response(client.post(endpoint, json={"content": QUESTION}), stream, free=False)
        assert len(trace) == 10 and all(method == "POST" for method, _ in trace)
        verify_round(tuple(result[k] for k in ("stage1", "stage2", "stage3", "metadata")), observed, free=False, title=True)
        assert storage.get_conversation(cid)["title"] == TITLE


@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_successful_catalog_is_not_reused(gateway_config, monkeypatch, tmp_path, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    configure(gateway_config, "canonical")
    trace, observed = observe(monkeypatch, "zero")
    with TestClient(main.app) as client:
        assert client.put("/api/settings", json={"profile": "free"}).status_code == 200
        cid = client.post("/api/conversations", json={}).json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        result = decode_response(client.post(endpoint, json={"content": QUESTION}), stream)
        verify_round(tuple(result[k] for k in ("stage1", "stage2", "stage3", "metadata")), observed, title=True)
        assert trace == [("GET", CATALOG)] + [("POST", PRIMARY)] * 7
        # A long persisted history would require a summary model call if the
        # preflight were placed after context preparation.
        storage.add_user_message(cid, "Earlier archive details: " + "x" * 7000)
        storage.add_assistant_message(cid, [], [], {"model": FREE_CHAIRMAN, "response": "Prior decision"})
        before = storage.get_conversation(cid)
        trace.clear()
        observed[0].clear()
        install_catalog(monkeypatch, "paid-completion", trace)
        response = client.post(endpoint, json={"content": "What about the second archive?"})
        assert trace == [("GET", CATALOG)] and not observed[0]
        assert response.status_code == 503
        assert storage.get_conversation(cid) == before
        assert client.get(f"/api/conversations/{cid}").json() == before


@pytest.mark.parametrize("cancel", [False, True], ids=["complete", "cancel"])
def test_reservation_during_catalog(gateway_config, monkeypatch, cancel):
    from backend import main
    configure(gateway_config, "canonical")
    trace, observed = observe(monkeypatch, "zero")
    started, proceed, finished = Event(), Event(), Event()
    original = httpx.HTTPTransport.handle_request

    def pause(transport, request):
        started.set()
        try:
            assert proceed.wait(3), "catalog did not unblock"
            return original(transport, request)
        finally:
            finished.set()

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", pause)

    async def run():
        task = asyncio.create_task(reserve(main))
        try:
            assert await asyncio.to_thread(started.wait, 2), "catalog preflight was not started"
            assert not observed[0] and main._active_council_runs == 1
            with pytest.raises(HTTPException) as err:
                main._update_when_idle(lambda: None)
            assert err.value.status_code == 409
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert main._active_council_runs == 0
                assert main._update_when_idle(lambda: "idle") == "idle"
            else:
                proceed.set()
                assert await task is True
                main._release_council_run()
        finally:
            proceed.set()
            if started.is_set():
                assert await asyncio.to_thread(finished.wait, 3)
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        assert trace == [("GET", CATALOG)] and not observed[0]
        assert main._active_council_runs == 0

    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
@pytest.mark.parametrize("conflict", ["endpoint", "missing-key"])
def test_catalog_not_called_before_static_guard(gateway_config, monkeypatch, tmp_path, stream, conflict):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    configure(gateway_config, "other-host" if conflict == "endpoint" else "canonical")
    trace, observed = observe(monkeypatch, "zero")
    with TestClient(main.app) as client:
        assert client.put("/api/settings", json={"profile": "free"}).status_code == 200
        if conflict == "missing-key":
            monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        cid = client.post("/api/conversations", json={}).json()["id"]
        before = storage.get_conversation(cid)
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        response = client.post(endpoint, json={"content": QUESTION})
        assert response.status_code == (409 if conflict == "endpoint" else 400)
        assert trace == [] and not observed[0]
        assert storage.get_conversation(cid) == before
        assert main._active_council_runs == 0
