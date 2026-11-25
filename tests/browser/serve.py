"""Test-only loopback host for the real desktop frontend and FastAPI application."""
import os
import socket
import sys
from pathlib import Path

import httpx
import uvicorn
from fastapi.staticfiles import StaticFiles


def deny_provider(*_args, **_kwargs):
    print("UI01: forbidden provider HTTP request", flush=True)
    raise RuntimeError("Provider HTTP is disabled for UI-01")


async def deny_async_provider(*args, **kwargs):
    deny_provider(*args, **kwargs)


def serve(dist, *, async_transport=deny_async_provider, wrap_app=None):
    # The caller sets cwd to an isolated copy of backend/configuration.
    sys.path.insert(0, str(Path.cwd()))
    httpx.HTTPTransport.handle_request = deny_provider
    httpx.AsyncHTTPTransport.handle_async_request = async_transport
    original_dns = socket.getaddrinfo

    def loopback_dns(host, *args, **kwargs):
        if host not in (None, "localhost", "127.0.0.1", "::1"):
            deny_provider()
        return original_dns(host, *args, **kwargs)

    socket.getaddrinfo = loopback_dns
    from backend.main import app

    app.mount("/app", StaticFiles(directory=dist, html=True), name="frontend")
    uvicorn.run(wrap_app(app) if wrap_app else app, host="127.0.0.1", port=8001, log_level="info")


if __name__ == "__main__":
    if not os.environ.get("LLM_COUNCIL_UI_DATA_DIR") or not os.environ.get("LLM_COUNCIL_CONFIG"):
        raise RuntimeError("Run the isolated browser harness, not the server directly")
    serve(sys.argv[1])
