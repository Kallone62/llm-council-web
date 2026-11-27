"""UI-12: real browser deletion, confirmation, stale-row failure and disk isolation."""
import json
import os
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from serve_ui12 import CAPTURE
from ui01 import BASE, DIST, ROOT, is_response, isolated_server, json_response, observed_browser

ARTIFACTS = ROOT / "browser-artifacts" / "ui12" / os.getenv("UI12_RUN", "normal")
RECORDS = (
    {"id": "b879c531-2c54-4bd1-9006-3a9838d81001", "created_at": "2025-01-01T00:00:00+00:00",
     "title": "Selected archive", "messages": [{"role": "user", "content": "Selected archive — Türkçe ✓"}]},
    {"id": "b879c531-2c54-4bd1-9006-3a9838d81002", "created_at": "2025-01-02T00:00:00+00:00",
     "title": "Other archive", "messages": [{"role": "user", "content": "Other archive — preserve until deletion"}]},
    {"id": "b879c531-2c54-4bd1-9006-3a9838d81003", "created_at": "2025-01-03T00:00:00+00:00",
     "title": "Protected archive", "messages": [{"role": "user", "content": "Never delete this archive"}]},
)
A, B, C = RECORDS


def snapshot(data):
    return {p.name: p.read_bytes() for p in data.iterdir()}


def filename(record):
    return record["id"] + ".json"


def detail(record):
    return f"/api/conversations/{record['id']}"


def listing(records):
    return [{"id": r["id"], "created_at": r["created_at"], "title": r["title"],
             "message_count": len(r["messages"])} for r in reversed(records)]


def assert_storage(client, data, before, records):
    assert snapshot(data) == before, "UI12: unrelated or retained storage changed"
    assert json_response(client.get("/api/conversations")) == listing(records)
    for record in records:
        assert json_response(client.get(detail(record))) == record


def select_button(page, record):
    return page.get_by_role("button", name=f"{record['title']} 1 messages", exact=True)


def delete_button(page, record):
    return page.get_by_role("button", name=f"Delete conversation: {record['title']}", exact=True)


def assert_selected(page, record):
    expect(select_button(page, record)).to_have_attribute("aria-current", "page")
    expect(page.locator(".user-message .markdown-content")).to_have_text(record["messages"][0]["content"])
    expect(page.locator(".assistant-message")).to_have_count(0)


def open_record(page, record):
    with page.expect_response(lambda r: is_response(r, "GET", detail(record))) as loaded:
        select_button(page, record).click()
    assert loaded.value.status == 200 and loaded.value.json() == record
    assert_selected(page, record)


def click_delete(page, record, accept):
    dialogs = []

    def respond(dialog):
        dialogs.append((dialog.type, dialog.message))
        if accept:
            dialog.accept()
        else:
            dialog.dismiss()

    page.once("dialog", respond)
    delete_button(page, record).click()
    assert dialogs == [("confirm", f'Delete "{record["title"]}" permanently? This cannot be undone.')], "UI12: wrong or absent confirmation"


def assert_deleted(client, data, before, target, remaining):
    after = snapshot(data)
    survivors = {name: raw for name, raw in before.items() if name != filename(target)}
    assert all(after.get(name) == raw for name, raw in survivors.items()), "UI12: another conversation was deleted or changed"
    assert filename(target) not in after, "UI12: target still on disk"
    assert_storage(client, data, survivors, remaining)
    missing = client.get(detail(target))
    assert missing.status_code == 404 and missing.json() == {"detail": "Conversation not found"}
    return survivors


def cancel_delete(page, client, data, before, requests):
    count = len(requests)
    click_delete(page, A, False)
    # A real browser GET barrier also lets any incorrectly launched DELETE be observed.
    response = page.evaluate("async () => (await fetch('/api/conversations')).json()")
    assert not requests[count:], "UI12: cancelled confirmation sent DELETE"
    assert response == listing(RECORDS)
    assert_storage(client, data, before, RECORDS)
    assert_selected(page, A)
    expect(delete_button(page, A)).to_be_enabled()


def stale_delete(page, client, data, work, before):
    # Emulate a file removed outside this page after the sidebar was loaded.
    # No API stub: the actual backend must return its real missing-record 404.
    target = data / filename(B)
    held = work / "held-stale-record.json"
    target.rename(held)
    try:
        with page.expect_response(lambda r: is_response(r, "DELETE", detail(B))) as deleted:
            click_delete(page, B, True)
        assert deleted.value.status == 404 and deleted.value.json() == {"detail": "Conversation not found"}
        expect(page.locator(".sidebar-error"), "UI12: deletion error not shown").to_have_text("Conversation not found")
        expect(delete_button(page, B)).to_be_enabled()
        expect(select_button(page, B), "UI12: failed deletion removed sidebar record").to_be_visible()
        assert_selected(page, A)
        assert_storage(client, data, {name: raw for name, raw in before.items() if name != filename(B)}, (A, C))
        assert held.read_bytes() == before[filename(B)]
    finally:
        held.rename(target)
    assert_storage(client, data, before, RECORDS)


def successful_delete(page, client, data, work, before, target, remaining):
    with page.expect_response(lambda r: r.request.method == "DELETE") as deleted:
        click_delete(page, target, True)
    response = deleted.value
    assert is_response(response, "DELETE", detail(target)), "UI12: DELETE addressed the wrong conversation"
    assert response.status == 204, "UI12: DELETE did not return 204"
    assert response.finished() is None
    captured = [json.loads(line) for line in (work / CAPTURE).read_text(encoding="utf-8").splitlines()]
    assert captured[-1] == {"path": detail(target), "status": 204, "body_hex": ""}, "UI12: DELETE did not return empty 204"
    after = assert_deleted(client, data, before, target, remaining)
    expect(select_button(page, target), "UI12: deleted conversation still in sidebar").to_have_count(0)
    expect(delete_button(page, target)).to_have_count(0)
    expect(page.locator(".sidebar-error")).to_have_count(0)
    for record in remaining:
        expect(select_button(page, record)).to_be_visible()
        expect(delete_button(page, record)).to_be_enabled()
    return after


def verify_console(errors):
    # Only Chromium's real 404 and App's expected error diagnostic are allowed.
    expected = "Failed to delete conversation: Error: Conversation not found"
    allowed = "Failed to load resource: the server responded with a status of 404 (Not Found)"
    assert any(error.splitlines()[0] == expected for error in errors), "UI12: missing deletion diagnostic"
    for error in errors:
        first, *stack = error.splitlines()
        assert first in (expected, allowed) and all(line.startswith("    at ") for line in stack), f"UI12: unexpected browser error: {error}"


def scenario(page, client, data, work, output, network, errors, forbidden):
    before = snapshot(data)
    assert_storage(client, data, before, RECORDS)
    requests = []
    page.on("request", lambda request: requests.append(request.url) if request.method == "DELETE" else None)
    with page.expect_response(lambda r: is_response(r, "GET", "/api/conversations")) as loaded, \
            page.expect_response(lambda r: is_response(r, "GET", "/api/settings")):
        assert page.goto(BASE + "/app/", wait_until="domcontentloaded").status == 200
    assert loaded.value.json() == listing(RECORDS)
    open_record(page, A)
    cancel_delete(page, client, data, before, requests)
    stale_delete(page, client, data, work, before)
    before = successful_delete(page, client, data, work, before, B, (A, C))
    assert_selected(page, A)
    before = successful_delete(page, client, data, work, before, A, (C,))
    expect(page.get_by_role("heading", name="Welcome to LLM Council"), "UI12: deleted active chat remained open").to_be_visible()
    expect(page.locator(".user-message, .assistant-message, .message-input")).to_have_count(0)
    expect(page.locator(".conversation-select[aria-current=page]")).to_have_count(0)
    page.screenshot(path=str(output / "after-delete.png"), full_page=True)
    with page.expect_response(lambda r: is_response(r, "GET", "/api/conversations")) as reloaded:
        page.reload(wait_until="domcontentloaded")
    assert reloaded.value.status == 200 and reloaded.value.json() == listing((C,))
    expect(page.locator(".conversation-select")).to_have_count(1)
    for record in (A, B):
        expect(select_button(page, record)).to_have_count(0)
        missing = client.delete(detail(record))
        assert missing.status_code == 404 and missing.json() == {"detail": "Conversation not found"}
    open_record(page, C)
    assert_storage(client, data, before, (C,))
    assert before == {filename(C): json.dumps(C, ensure_ascii=False, indent=2).encode("utf-8")}
    assert requests == [BASE + detail(B), BASE + detail(B), BASE + detail(A)], "UI12: unexpected DELETE requests"
    assert [r for r in network if r["method"] != "GET"] == [
        {"method": "DELETE", "path": detail(B), "status": 404},
        {"method": "DELETE", "path": detail(B), "status": 204},
        {"method": "DELETE", "path": detail(A), "status": 204}]
    assert not forbidden
    verify_console(errors)
    page.screenshot(path=str(output / "after-reload.png"), full_page=True)
    (output / "remaining.json").write_bytes(before[filename(C)])


def main():
    assert (DIST / "index.html").is_file(), "UI12: production frontend build is missing"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="council-ui12-") as directory, sync_playwright() as playwright:
        work = Path(directory)
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            with isolated_server(work, ARTIFACTS, server_script="serve_ui12.py") as (client, data):
                for record in RECORDS:
                    (data / filename(record)).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                with observed_browser(browser, ARTIFACTS) as (page, network, errors, forbidden):
                    scenario(page, client, data, work, ARTIFACTS, network, errors, forbidden)
        finally:
            browser.close()
            if (work / CAPTURE).is_file():
                shutil.copyfile(work / CAPTURE, ARTIFACTS / CAPTURE)
    print("UI12 PASS: cancel, real 404, active/non-active deletion, reload and preserved other record", flush=True)


if __name__ == "__main__":
    main()
