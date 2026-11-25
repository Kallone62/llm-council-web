"""UI-01: real Chromium, production React, FastAPI and isolated JSON storage."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://localhost:8001"
DIST = ROOT / "frontend" / "dist"
ARTIFACTS = ROOT / "browser-artifacts" / os.getenv("UI01_RUN", "normal")
EXISTING = {
    "id": "a159c2df-205f-45e1-beda-ad7e8df45c1f",
    "created_at": "2025-01-01T00:00:00+00:00",
    "title": "Existing archive conversation",
    "messages": [{"role": "user", "content": "Keep this original record."}],
}


@contextmanager
def isolated_server(work, output):
    # Never reuse the real application's .env, YAML, data, or provider keys.
    shutil.copytree(ROOT / "backend", work / "backend", ignore=shutil.ignore_patterns("__pycache__"))
    config = work / "llm_council.yaml"
    shutil.copyfile(ROOT / "llm_council.yaml", config)
    data = work / "conversations"
    data.mkdir()
    home = work / "home"
    home.mkdir()
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "PYTHONPATH", "SYSTEMROOT") if key in os.environ}
    env.update(HOME=str(home), LLM_COUNCIL_CONFIG=str(config),
               LLM_COUNCIL_UI_DATA_DIR=str(data), OPENROUTER_API_KEY="")
    with socket.socket() as probe:
        # A prior isolated server may leave loopback connections in TIME_WAIT.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", 8001))
    log = output / "backend.log"
    with log.open("w", encoding="utf-8") as handle:
        server = subprocess.Popen([sys.executable, str(Path(__file__).with_name("serve.py")), str(DIST)],
                                  cwd=work, env=env, stdout=handle, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=BASE, trust_env=False, timeout=2) as client:
                deadline = time.monotonic() + 30
                while True:
                    assert server.poll() is None, "UI01: backend exited before readiness"
                    try:
                        response = client.get("/")
                        if response.status_code == 200 and response.json().get("status") == "ok":
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, "UI01: backend readiness timeout"
                    time.sleep(.1)
                settings = client.get("/api/settings")
                assert settings.status_code == 200, "UI01: settings API unavailable"
                assert Path(settings.json()["config_path"]) == config, "UI01: wrong backend/configuration"
                assert settings.json()["api_key_configured"] is False, "UI01: unexpected provider key"
                yield client, data
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    assert "UI01: forbidden provider HTTP request" not in log.read_text(encoding="utf-8"), "UI01: unexpected provider traffic"


def json_response(response):
    assert response.status_code == 200, f"UI01: API status {response.status_code} at {response.url.path}"
    return response.json()


def is_response(response, method, path):
    return response.request.method == method and urlsplit(response.url).path == path


def check_conversation(page, client, data, seeded):
    initial = json_response(client.get("/api/conversations"))
    assert {row["id"] for row in initial} == ({EXISTING["id"]} if seeded else set())
    before = {path.name: path.read_bytes() for path in data.glob("*.json")}
    with page.expect_response(lambda r: is_response(r, "GET", "/api/conversations")) as listing, \
            page.expect_response(lambda r: is_response(r, "GET", "/api/settings")) as settings:
        response = page.goto(BASE + "/app/", wait_until="domcontentloaded")
        assert response and response.status == 200, "UI01: frontend unavailable"
    assert listing.value.status == 200 and listing.value.json() == initial
    assert settings.value.status == 200 and settings.value.json()["api_key_configured"] is False
    expect(page.get_by_role("heading", name="LLM Council", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Welcome to LLM Council")).to_be_visible()
    if seeded:
        expect(page.get_by_role("button", name="Existing archive conversation 1 messages")).to_be_visible()
    else:
        expect(page.get_by_text("No conversations yet", exact=True)).to_be_visible()

    button = page.get_by_role("button", name="+ New Conversation", exact=True)
    expect(button).to_be_enabled()
    with page.expect_response(lambda r: is_response(r, "POST", "/api/conversations")) as created:
        button.click()
    response = created.value
    assert response.status == 200, "UI01: create API failed"
    conversation = response.json()
    cid = conversation["id"]
    assert str(uuid.UUID(cid)) == cid and cid not in {row["id"] for row in initial}
    assert conversation["title"] == "New Conversation" and conversation["messages"] == []
    # Check independent storage and API first; a returned POST object alone is not success.
    path = data / f"{cid}.json"
    assert path.is_file(), "UI01: new conversation was not persisted"
    assert json.loads(path.read_text(encoding="utf-8")) == conversation, "UI01: POST/disk mismatch"
    assert json_response(client.get(f"/api/conversations/{cid}")) == conversation
    after = json_response(client.get("/api/conversations"))
    assert {row["id"] for row in after} == {row["id"] for row in initial} | {cid}
    assert next(row for row in after if row["id"] == cid) == {
        "id": cid, "created_at": conversation["created_at"], "title": "New Conversation", "message_count": 0}
    assert [row for row in after if row["id"] != cid] == initial
    assert {p.name for p in data.glob("*.json")} == set(before) | {path.name}
    assert all((data / name).read_bytes() == contents for name, contents in before.items())
    if seeded:
        assert json_response(client.get(f"/api/conversations/{EXISTING['id']}")) == EXISTING
        expect(page.get_by_role("button", name="Existing archive conversation 1 messages")).to_be_visible()
    new_item = page.get_by_role("button", name="New Conversation 0 messages", exact=True)
    expect(new_item, "UI01: new sidebar conversation missing").to_be_visible()
    expect(new_item, "UI01: new sidebar conversation not selected").to_have_attribute("aria-current", "page")
    expect(page.get_by_role("heading", name="Start a conversation")).to_be_visible()
    expect(page.get_by_placeholder("Ask a question or continue this conversation... (Shift+Enter for new line)")).to_be_editable()
    expect(page.get_by_text("No conversations yet", exact=True)).to_have_count(0)
    return cid


def browser_case(browser, seeded):
    output = ARTIFACTS / ("seeded" if seeded else "empty")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="council-ui01-") as directory:
        with isolated_server(Path(directory), output) as (client, data):
            if seeded:
                (data / f"{EXISTING['id']}.json").write_text(json.dumps(EXISTING), encoding="utf-8")
            context = browser.new_context(service_workers="block")
            network, errors, forbidden = [], [], []

            def guard(route):
                url = urlsplit(route.request.url)
                if url.scheme == "http" and url.netloc == "localhost:8001":
                    route.continue_()
                else:
                    forbidden.append(url.hostname or url.scheme)
                    route.abort()

            context.route("**/*", guard)
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            page.set_default_timeout(8000)
            page.on("response", lambda r: network.append({"method": r.request.method,
                       "path": urlsplit(r.url).path, "status": r.status}))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            try:
                cid = check_conversation(page, client, data, seeded)
                assert sum(r["method"] == "POST" and r["path"] == "/api/conversations" for r in network) == 1
                assert any(r == {"method": "GET", "path": f"/api/conversations/{cid}", "status": 200} for r in network)
                assert not forbidden, f"UI01: external browser traffic: {forbidden}"
                assert not errors, f"UI01: browser errors: {errors}"
            except BaseException:
                page.screenshot(path=str(output / "failure.png"), full_page=True)
                context.tracing.stop(path=str(output / "trace.zip"))
                raise
            else:
                context.tracing.stop()
            finally:
                (output / "network.json").write_text(json.dumps(network, indent=2), encoding="utf-8")
                (output / "console.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")
                context.close()
    print(f"UI01 PASS: {'seeded' if seeded else 'empty'}", flush=True)


def main():
    assert (DIST / "index.html").is_file(), "UI01: production frontend build is missing"
    with sync_playwright() as playwright:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            browser_case(browser, False)
            browser_case(browser, True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
