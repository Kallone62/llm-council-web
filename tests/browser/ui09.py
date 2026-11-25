"""UI-09: real HTTP rejection and terminal SSE error; no phantom success."""
import json
import os
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from serve_ui09 import CHAIRMAN, HTTP_ERROR, JOURNAL, LONG_QUESTION, OLD_ANSWER, QUESTION, SSE_ERROR, TITLE_MODEL
from stream_observer_ui09 import STREAM_CAPTURE
from ui01 import BASE, DIST, EXISTING, ROOT, is_response, isolated_server, json_response, observed_browser
from ui04 import configure

ARTIFACTS = ROOT / "browser-artifacts" / "ui09" / os.getenv("UI09_RUN", "normal")
RECORD = {
    "id": "6b3c468d-5d57-44cf-849a-53302fb92b77",
    "created_at": "2025-01-02T00:00:00+00:00",
    "title": "UI09 preserved history",
    "messages": [
        {"role": "user", "content": LONG_QUESTION},
        {"role": "assistant", "stage1": [], "stage2": [],
         "stage3": {"model": CHAIRMAN, "response": OLD_ANSWER}, "metadata": {}},
    ],
}


def journal(work):
    path = work / JOURNAL
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def select_record(page):
    detail = f"/api/conversations/{RECORD['id']}"
    with page.expect_response(lambda r: is_response(r, "GET", detail)) as loaded:
        page.get_by_role("button", name="UI09 preserved history 2 messages", exact=True).click()
    assert loaded.value.status == 200 and loaded.value.json() == RECORD
    expect(page.locator(".assistant-message .final-text")).to_have_text(OLD_ANSWER)
    expect(page.locator(".user-message")).to_have_count(1)


def assert_persistence(client, data, before):
    assert {p.name: p.read_bytes() for p in data.glob("*.json")} == before, "UI09: rejected round changed disk"
    for record in (RECORD, EXISTING):
        assert json_response(client.get(f"/api/conversations/{record['id']}")) == record
    assert {r["id"]: r["message_count"] for r in json_response(client.get("/api/conversations"))} == {
        RECORD["id"]: 2, EXISTING["id"]: 1}


def scenario(page, client, data, work, output, network, errors, forbidden, case):
    if case == "http":
        settings = json_response(client.put("/api/settings", json={"profile": "free"}))
        assert settings["profile"] == "free" and settings["api_key_configured"] is False
    else:
        configure(client)
    for record in (RECORD, EXISTING):
        (data / f"{record['id']}.json").write_text(json.dumps(record), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in data.glob("*.json")}
    with page.expect_response(lambda r: is_response(r, "GET", "/api/settings")) as settings_response:
        assert page.goto(BASE + "/app/", wait_until="domcontentloaded").status == 200
    assert settings_response.value.json()["api_key_configured"] == (case == "sse")
    select_record(page)
    entry = page.get_by_placeholder("Ask a question or continue this conversation... (Shift+Enter for new line)")
    expect(entry).to_be_editable()
    entry.fill(QUESTION)
    detail = f"/api/conversations/{RECORD['id']}"
    endpoint = detail + "/message/stream"
    with page.expect_response(lambda r: is_response(r, "POST", endpoint)) as sent:
        page.get_by_role("button", name="Send", exact=True).click()
    response = sent.value
    assert response.request.post_data_json == {"content": QUESTION, "verdict_type": "synthesis", "include_dissent": False}
    if case == "http":
        assert response.status == 400 and response.json() == {"detail": HTTP_ERROR}
        evidence = response.json()
        message = HTTP_ERROR
    else:
        assert response.status == 200 and response.headers["content-type"].startswith("text/event-stream")
        expect(page.locator(".engine-progress-event").filter(has_text="Conversation memory · AI summary: running")).to_be_visible()
        expect(entry).to_be_disabled()
        expect(page.locator(".loading-indicator")).to_be_visible()
        expect(page.get_by_role("button", name="Send", exact=True)).to_be_disabled()
        (work / "release-memory").touch()
        # The production client cancels its reader on a terminal error. Chromium
        # may discard the response body, so observe the actual ASGI send bytes.
        message = SSE_ERROR
    expect(page.locator(".council-error"), "UI09: meaningful error missing").to_have_count(1)
    expect(page.locator(".council-error"), "UI09: meaningful error missing").to_be_visible()
    expect(page.locator(".council-error strong")).to_have_text("Council request failed")
    expect(page.locator(".council-error span"), "UI09: meaningful error mismatch").to_have_text(message)
    expect(entry, "UI09: loading did not end").to_be_editable()
    expect(page.locator(".loading-indicator, .stage-loading"), "UI09: loading did not end").to_have_count(0)
    expect(page.locator(".assistant-message")).to_have_count(2)
    failed = page.locator(".assistant-message").last
    expect(failed.locator(".stage3, .final-text, .verdict-summary"), "UI09: false final displayed").to_have_count(0)
    expect(page.locator(".assistant-message .final-text")).to_have_text(OLD_ANSWER)
    expect(page.locator(".user-message .markdown-content").last).to_have_text(QUESTION)
    entry.fill("Retry is available")
    expect(page.get_by_role("button", name="Send", exact=True)).to_be_enabled()
    if case == "sse":
        expect(failed.locator(".engine-progress-event")).to_have_count(2)
        expect(failed.locator(".engine-progress-event").last).to_contain_text("Conversation memory · AI summary: failed")
    assert_persistence(client, data, before)
    assert journal(work) == ([] if case == "http" else [{"stage": "memory", "model": TITLE_MODEL}]), "UI09: unexpected provider traffic"
    assert [r for r in network if r["method"] == "POST"] == [{"method": "POST", "path": endpoint, "status": 400 if case == "http" else 200}]
    # The failed request cannot trigger a successful result reload.
    assert [r for r in network if r["method"] == "GET" and r["path"] == detail] == [
        {"method": "GET", "path": detail, "status": 200}], "UI09: false success reload"
    # A deliberate full reload must show only the previously persisted history.
    with page.expect_response(lambda r: is_response(r, "GET", "/api/conversations")):
        page.reload(wait_until="domcontentloaded")
    select_record(page)
    expect(page.locator(".assistant-message")).to_have_count(1)
    expect(page.locator(".council-error")).to_have_count(0)
    assert_persistence(client, data, before)
    assert json_response(client.put("/api/settings", json={"profile": "quick"}))["profile"] == "quick"
    if case == "sse":
        evidence = [json.loads(line[6:]) for line in (work / STREAM_CAPTURE).read_text(encoding="utf-8").splitlines()
                    if line.startswith("data: ")]
        assert [event["type"] for event in evidence] == ["engine_progress", "engine_progress", "error"]
        assert [(e["data"]["stage"], e["data"]["state"]) for e in evidence[:2]] == [("memory", "started"), ("memory", "failed")]
        assert evidence[-1] == {"type": "error", "message": SSE_ERROR}
    else:
        assert not (work / STREAM_CAPTURE).exists(), "UI09: HTTP rejection unexpectedly produced SSE"
    (output / "response.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    allowed = {"Failed to send message: Error: " + message, "Failed to send message: " + message}
    if case == "sse":
        allowed.add("Stream error: " + message)
    else:
        allowed.add("Failed to load resource: the server responded with a status of 400 (Bad Request)")
    # Chromium formats a logged Error object as its message plus JS stack.
    # Accept only the exact expected first line and genuine stack frames.
    def expected_console(error):
        first, *stack = error.splitlines()
        return first in allowed and (not stack or (
            first == "Failed to send message: Error: " + message
            and all(frame.startswith("    at ") for frame in stack)))

    assert not forbidden and all(expected_console(error) for error in errors), f"UI09: unexpected browser errors: {errors}"


def main():
    assert (DIST / "index.html").is_file(), "UI09: production frontend build is missing"
    with sync_playwright() as playwright:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            for case in ("http", "sse"):
                output = ARTIFACTS / case
                output.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="council-ui09-") as directory:
                    work = Path(directory)
                    try:
                        with isolated_server(work, output, server_script="serve_ui09.py") as (client, data):
                            with observed_browser(browser, output) as (page, network, errors, forbidden):
                                scenario(page, client, data, work, output, network, errors, forbidden, case)
                    finally:
                        for artifact in (JOURNAL, STREAM_CAPTURE):
                            if (work / artifact).is_file():
                                shutil.copyfile(work / artifact, output / artifact)
                print(f"UI09 PASS: {case}", flush=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
