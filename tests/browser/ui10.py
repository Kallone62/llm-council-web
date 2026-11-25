"""UI-10: real browser/native Council, missing or truncated terminal frame."""
import json
import os
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from serve_ui04 import FINAL, JOURNAL, QUESTION, TITLE
from stream_cut_ui10 import DELIVERED, MODE, UPSTREAM
from ui01 import BASE, DIST, EXISTING, ROOT, is_response, isolated_server, json_response, observed_browser
from ui04 import EXPECTED_PROGRESS, assert_display, assert_live, assert_provider, assert_result, configure

ARTIFACTS = ROOT / "browser-artifacts" / "ui10" / os.getenv("UI10_RUN", "normal")
MODES = ("intact", "eof", "truncated")
ERRORS = {
    "eof": "Council stream: connection closed before completion.",
    "truncated": "Council stream: truncated event frame.",
}


def stream_evidence(work, mode):
    upstream = (work / UPSTREAM).read_bytes()
    delivered = (work / DELIVERED).read_bytes()
    events = [json.loads(line[6:]) for line in upstream.splitlines() if line.startswith(b"data: ")]
    assert [event["type"] for event in events] == ["engine_progress"] * len(EXPECTED_PROGRESS) + [
        "stage1_complete", "stage2_complete", "stage3_complete", "complete", "title_complete"], "UI10: upstream terminal contract mismatch"
    progress = [event["data"] for event in events[:-5]]
    assert [(e["stage"], e["state"]) for e in progress] == EXPECTED_PROGRESS
    metadata = events[-2]["metadata"]
    assert events[-4]["metadata"] == events[-3]["metadata"] == metadata
    assert metadata["ui_runtime_progress"] == progress
    assert events[-1]["data"] == {"title": TITLE}
    if mode == "intact":
        assert delivered == upstream
    else:
        boundary = upstream.index(b'data: {"type": "complete"')
        end = boundary if mode == "eof" else boundary + len(b'data: {"type": "complete"')
        assert delivered == upstream[:end], "UI10: transport cut did not preserve the exact upstream prefix"
    payload = {"stage1": events[-5]["data"], "stage2": events[-4]["data"],
               "stage3": events[-3]["data"], "metadata": metadata}
    assert_result(payload)
    return payload


def assert_interrupted(page, entry, network, detail, initial_gets, mode):
    assert sum(r["method"] == "GET" and r["path"] in (detail, "/api/conversations")
               for r in network) == initial_gets, "UI10: invalid stream caused success reload"
    expect(page.locator(".stage3, .final-text, .verdict-summary"),
           "UI10: final shown before terminal").to_have_count(0)
    expect(page.locator(".council-error"), "UI10: missing interruption error").to_be_visible()
    expect(page.locator(".council-error strong")).to_have_text("Council request failed")
    expect(page.locator(".council-error span")).to_have_text(ERRORS[mode])
    expect(page.locator(".stage1")).to_be_visible()
    expect(page.locator(".stage2")).to_be_visible()
    expect(page.locator(".engine-progress-event")).to_have_count(len(EXPECTED_PROGRESS))
    entry.fill("Retry is available")
    expect(page.get_by_role("button", name="Send", exact=True)).to_be_enabled()


def assert_console(errors, mode):
    if mode == "intact":
        assert not errors, f"UI10: unexpected browser errors: {errors}"
        return
    expected = "Failed to send message: Error: " + ERRORS[mode]
    assert errors, "UI10: missing browser interruption diagnostic"
    for error in errors:
        first, *stack = error.splitlines()
        assert first == expected and all(frame.startswith("    at ") for frame in stack), f"UI10: unexpected browser error: {error}"


def scenario(page, client, data, work, output, network, errors, forbidden, mode):
    configure(client)
    with page.expect_response(lambda r: is_response(r, "GET", "/api/settings")) as settings, \
            page.expect_response(lambda r: is_response(r, "GET", "/api/conversations")):
        assert page.goto(BASE + "/app/", wait_until="domcontentloaded").status == 200
    assert settings.value.json()["api_key_configured"] is True
    with page.expect_response(lambda r: is_response(r, "POST", "/api/conversations")) as created:
        page.get_by_role("button", name="+ New Conversation", exact=True).click()
    cid = created.value.json()["id"]
    assert created.value.status == 200
    detail = f"/api/conversations/{cid}"
    entry = page.get_by_placeholder("Ask a question or continue this conversation... (Shift+Enter for new line)")
    expect(entry).to_be_editable()
    initial_gets = sum(r["method"] == "GET" and r["path"] in (detail, "/api/conversations") for r in network)
    entry.fill(QUESTION)
    with page.expect_response(lambda r: is_response(r, "POST", detail + "/message/stream")) as streamed:
        page.get_by_role("button", name="Send", exact=True).click()
    response = streamed.value
    assert response.status == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.request.post_data_json == {"content": QUESTION, "verdict_type": "synthesis", "include_dissent": False}
    assert_live(page, work, entry)
    expect(entry, "UI10: loading did not end").to_be_editable()
    expect(page.locator(".loading-indicator, .stage-loading"), "UI10: loading did not end").to_have_count(0)
    if mode == "intact":
        expect(page.locator(".stage3 .final-text"), "UI10: intact stream did not complete").to_have_text(FINAL)
        expect(page.get_by_role("button", name=f"{TITLE} 2 messages", exact=True)).to_be_visible()
    else:
        assert_interrupted(page, entry, network, detail, initial_gets, mode)
    expect(page.locator(".user-message .markdown-content")).to_have_text(QUESTION)
    expect(page.locator(".assistant-message")).to_have_count(1)
    page.screenshot(path=str(output / "before-reload.png"), full_page=True)
    # The native result is saved before complete. Do not confuse transport failure
    # with a failed Council round, or retry an already-persisted result.
    assert json_response(client.put("/api/settings", json={"profile": "quick"}))["profile"] == "quick"
    payload = stream_evidence(work, mode)
    record = json_response(client.get(detail))
    disk_path = data / f"{cid}.json"
    before = disk_path.read_bytes()
    assert record == json.loads(before) and record["title"] == TITLE
    assert record["messages"] == [{"role": "user", "content": QUESTION},
                                  {"role": "assistant", **payload}], "UI10: saved result mismatch"
    assert_provider(work)
    if mode == "intact":
        assert_display(page, payload)
        assert any(r["method"] == "GET" and r["path"] == detail for r in network)
    assert [r for r in network if r["method"] == "POST"] == [
        {"method": "POST", "path": "/api/conversations", "status": 200},
        {"method": "POST", "path": detail + "/message/stream", "status": 200}]
    with page.expect_response(lambda r: is_response(r, "GET", "/api/conversations")) as listing:
        page.reload(wait_until="domcontentloaded")
    assert {r["id"]: r["message_count"] for r in listing.value.json()} == {cid: 2, EXISTING["id"]: 1}
    with page.expect_response(lambda r: is_response(r, "GET", detail)) as loaded:
        page.get_by_role("button", name=f"{TITLE} 2 messages", exact=True).click()
    assert loaded.value.status == 200 and loaded.value.json() == record
    assert_display(page, payload)
    expect(page.locator(".user-message .markdown-content")).to_have_text(QUESTION)
    expect(page.locator(".assistant-message")).to_have_count(1)
    expect(entry).to_be_editable()
    assert disk_path.read_bytes() == before and json_response(client.get(detail)) == record
    assert not forbidden, "UI10: forbidden browser traffic"
    assert_console(errors, mode)
    page.screenshot(path=str(output / "after-reload.png"), full_page=True)
    (output / "record.json").write_text(json.dumps(record, indent=2), encoding="utf-8")


def browser_case(browser, mode):
    output = ARTIFACTS / mode
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="council-ui10-") as directory:
        work = Path(directory)
        (work / MODE).write_text(mode, encoding="utf-8")
        try:
            with isolated_server(work, output, server_script="serve_ui10.py") as (client, data):
                old = data / f"{EXISTING['id']}.json"
                old.write_text(json.dumps(EXISTING), encoding="utf-8")
                before = old.read_bytes()
                with observed_browser(browser, output) as (page, network, errors, forbidden):
                    scenario(page, client, data, work, output, network, errors, forbidden, mode)
                    assert old.read_bytes() == before and len(list(data.glob("*.json"))) == 2
                    assert json_response(client.get(f"/api/conversations/{EXISTING['id']}")) == EXISTING
        finally:
            for artifact in (UPSTREAM, DELIVERED, JOURNAL):
                if (work / artifact).is_file():
                    shutil.copyfile(work / artifact, output / artifact)
    print(f"UI10 PASS: {mode}", flush=True)


def main():
    assert (DIST / "index.html").is_file(), "UI10: production frontend build is missing"
    with sync_playwright() as playwright:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            for mode in MODES:
                browser_case(browser, mode)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
