"""UI-14: settings/key rejected during a real Free round, accepted after release."""
import json
import os
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from serve_ui14 import MODELS, KEY, NEW_KEY, QUESTION, JOURNAL, CATALOG, ENDPOINT, REVIEW, FINAL, candidate
from ui01 import BASE, DIST, ROOT, EXISTING, isolated_server, observed_browser, is_response, json_response
from ui03 import SETTINGS, assert_effective, assert_disk, profile_select, check_notice, NOTICE, PROFILES
from ui04 import progress_locator, EXPECTED_PROGRESS

ARTIFACTS = ROOT / "browser-artifacts" / "ui14" / os.getenv("UI14_RUN", "normal")
KEY_PATH = SETTINGS + "/openrouter-key"
CONFLICT = "Settings cannot be changed while a Council round is running."


def journal(work):
    path = work / JOURNAL
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def assert_safe(rows):
    for row in rows:
        if row["phase"] == "request":
            assert row["url"] == ENDPOINT and row["model"] in MODELS and row["original_key"] is True, "UI14: unsafe provider traffic"


def wait_gate(work, stage, models):
    deadline = time.monotonic() + 8
    while True:
        rows = journal(work)
        assert_safe(rows)
        entered = [r["model"] for r in rows if r["phase"] == "entered" and r["stage"] == stage]
        if Counter(entered) == Counter(models):
            assert not any(r["phase"] == "response" and r["stage"] == stage for r in rows)
            return
        assert time.monotonic() < deadline, f"UI14: actual provider gate not reached: {stage}"
        time.sleep(.02)


def assert_unchanged(client, work, initial, yaml_bytes, env_bytes):
    assert (work / "llm_council.yaml").read_bytes() == yaml_bytes, "UI14: rejected settings changed disk"
    assert (work / ".env").read_bytes() == env_bytes, "UI14: rejected key changed disk"
    assert json_response(client.get(SETTINGS)) == initial, "UI14: rejected update changed effective settings"


def open_settings(page):
    with page.expect_response(lambda r: is_response(r, "GET", SETTINGS)) as loaded:
        page.get_by_role("button", name="⚙ Settings").click()
    assert loaded.value.status == 200
    expect(profile_select(page)).to_have_value("free")


def reject_browser(page, path, button, marker):
    with page.expect_response(lambda r: is_response(r, "PUT", path)) as result:
        page.get_by_role("button", name=button, exact=True).first.click()
    assert result.value.status == 409, marker
    assert result.value.json() == {"detail": CONFLICT}
    expect(page.locator(".settings-error"), "UI14: settings conflict hidden").to_have_text(CONFLICT)
    expect(page.locator(".settings-notice")).to_have_count(0)
    return result.value.request.post_data_json


def assert_provider(work):
    rows = journal(work)
    assert_safe(rows)
    assert [r for r in rows if r["phase"] == "catalog"] == [{"phase": "catalog", "url": CATALOG}]
    expected = [(s, m) for s in ("stage1", "stage2") for m in MODELS] + [("stage3", MODELS[0])]
    assert Counter((r["stage"], r["model"]) for r in rows if r["phase"] == "entered") == Counter(expected)
    assert Counter((r["stage"], r["model"]) for r in rows if r["phase"] == "response") == Counter(expected)
    assert Counter(r["model"] for r in rows if r["phase"] == "request") == Counter(m for _, m in expected)
    assert len(rows) == 1 + 3 * len(expected), "UI14: extra provider traffic"


def assert_result(page, client, data, cid, response):
    assert response.status == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.body().decode("utf-8").splitlines() if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["engine_progress"] * 8 + [
        "stage1_complete", "stage2_complete", "stage3_complete", "complete"]
    progress = [e["data"] for e in events[:8]]
    assert [(e["stage"], e["state"]) for e in progress] == EXPECTED_PROGRESS
    payload = {key: events[index]["data"] for index, key in enumerate(("stage1", "stage2", "stage3"), 8)}
    payload["metadata"] = events[-1]["metadata"]
    assert {r["model"]: r["response"] for r in payload["stage1"]} == {m: candidate(m) for m in MODELS}
    assert {r["model"] for r in payload["stage2"]} == set(MODELS)
    assert all(r["ranking"] == REVIEW for r in payload["stage2"])
    assert payload["stage3"] == {"model": MODELS[0], "response": FINAL}
    assert payload["metadata"]["peer_review"]["state"] == "completed"
    assert payload["metadata"]["ui_runtime_progress"] == progress
    record = json_response(client.get(f"/api/conversations/{cid}"))
    assert record == json.loads((data / f"{cid}.json").read_text(encoding="utf-8"))
    assert record["title"] == QUESTION and record["messages"] == [
        {"role": "user", "content": QUESTION}, {"role": "assistant", **payload}]
    expect(page.locator(".stage3 .final-text")).to_have_text(FINAL)
    expect(page.locator(".loading-indicator, .stage-loading, .council-error, .stage3-error")).to_have_count(0)
    return record


def scenario(page, client, data, work, output):
    assert json_response(client.put(KEY_PATH, json={"api_key": KEY})) == {"configured": True}
    initial = json_response(client.get(SETTINGS))
    assert initial["profile"] == "free" and initial["models"] == MODELS and initial["api_key_configured"] is True
    yaml_bytes = (work / "llm_council.yaml").read_bytes()
    env_bytes = (work / ".env").read_bytes()
    assert env_bytes == f"OPENROUTER_API_KEY={KEY}\n".encode()
    page.goto(BASE + "/app/", wait_until="domcontentloaded")
    check_notice(page, NOTICE, True)
    with page.expect_response(lambda r: is_response(r, "POST", "/api/conversations")) as created:
        page.get_by_role("button", name="+ New Conversation", exact=True).click()
    assert created.value.status == 200
    cid = created.value.json()["id"]
    stream_path = f"/api/conversations/{cid}/message/stream"
    entry = page.get_by_placeholder("Ask a question or continue this conversation... (Shift+Enter for new line)")
    entry.fill(QUESTION)
    with page.expect_response(lambda r: is_response(r, "POST", stream_path)) as streamed:
        page.get_by_role("button", name="Send", exact=True).click()
    assert streamed.value.status == 200
    wait_gate(work, "stage1", MODELS)
    expect(entry).to_be_disabled()
    expect(progress_locator(page, "stage1", "started")).to_be_visible()
    expect(progress_locator(page, "stage1", "completed")).to_have_count(0)
    open_settings(page)
    profile_select(page).select_option("reasoning")
    request = reject_browser(page, SETTINGS, "Save settings", "UI14: active profile update accepted")
    assert request["profile"] == "reasoning"
    assert_unchanged(client, work, initial, yaml_bytes, env_bytes)
    page.locator('input[type="password"]').fill(NEW_KEY)
    request = reject_browser(page, KEY_PATH, "Replace key", "UI14: active key update accepted")
    assert request == {"api_key": NEW_KEY}
    assert_unchanged(client, work, initial, yaml_bytes, env_bytes)
    page.screenshot(path=str(output / "conflict.png"), full_page=True)
    page.locator(".conversation-select").filter(has_text="New Conversation").click()
    check_notice(page, NOTICE, True)
    expect(entry, "UI14: active round lost loading state").to_be_disabled()
    expect(progress_locator(page, "stage1", "started")).to_be_visible()
    (work / "release-stage1").touch()

    wait_gate(work, "stage3", [MODELS[0]])
    expect(progress_locator(page, "stage3", "started")).to_be_visible()
    expect(entry).to_be_disabled()
    # An independent API client must also be denied at the last native stage.
    for path, body, marker in (
        (SETTINGS, {"profile": "quick"}, "UI14: late profile update accepted"),
        (KEY_PATH, {"api_key": NEW_KEY}, "UI14: late key update accepted"),
    ):
        response = client.put(path, json=body)
        assert response.status_code == 409, marker
        assert response.json() == {"detail": CONFLICT}
        assert_unchanged(client, work, initial, yaml_bytes, env_bytes)
    (work / "release-stage3").touch()
    expect(entry, "UI14: round did not finish").to_be_editable()
    assert_provider(work)
    record = assert_result(page, client, data, cid, streamed.value)
    assert_unchanged(client, work, initial, yaml_bytes, env_bytes)
    page.screenshot(path=str(output / "completed.png"), full_page=True)

    open_settings(page)
    profile_select(page).select_option("reasoning")
    with page.expect_response(lambda r: is_response(r, "PUT", SETTINGS)) as saved:
        page.get_by_role("button", name="Save settings", exact=True).first.click()
    assert saved.value.status == 200, "UI14: idle profile update rejected"
    effective = saved.value.json()
    assert effective["profile"] == "reasoning" and effective["models"] == PROFILES["reasoning"][0]
    assert effective["resolved_chairman"] == PROFILES["reasoning"][1] and effective["use_profile_models"] is True
    assert_disk(work / "llm_council.yaml", "reasoning", "reasoning")
    assert json_response(client.get(SETTINGS)) == effective
    page.locator('input[type="password"]').fill(NEW_KEY)
    with page.expect_response(lambda r: is_response(r, "PUT", KEY_PATH)) as replaced:
        page.get_by_role("button", name="Replace key", exact=True).click()
    assert replaced.value.status == 200, "UI14: idle key update rejected"
    assert replaced.value.json() == {"configured": True}
    assert (work / ".env").read_bytes() == f"OPENROUTER_API_KEY={NEW_KEY}\n".encode()
    page.get_by_role("button", name=f"{QUESTION} 2 messages", exact=True).click()
    check_notice(page, NOTICE, False)
    expect(page.locator(".stage3 .final-text")).to_have_text(FINAL)
    assert json_response(client.get(f"/api/conversations/{cid}")) == record
    assert_provider(work)
    return stream_path


def browser_case(browser):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="council-ui14-") as directory:
        work = Path(directory)
        try:
            with isolated_server(work, ARTIFACTS, server_script="serve_ui14.py") as (client, data):
                old = data / f"{EXISTING['id']}.json"
                old.write_text(json.dumps(EXISTING), encoding="utf-8")
                before = old.read_bytes()
                with observed_browser(browser, ARTIFACTS) as (page, network, errors, forbidden):
                    stream_path = scenario(page, client, data, work, ARTIFACTS)
                    assert old.read_bytes() == before and len(list(data.glob("*.json"))) == 2
                    assert [r for r in network if r["method"] not in ("GET", "HEAD")] == [
                        {"method": "POST", "path": "/api/conversations", "status": 200},
                        {"method": "POST", "path": stream_path, "status": 200},
                        {"method": "PUT", "path": SETTINGS, "status": 409},
                        {"method": "PUT", "path": KEY_PATH, "status": 409},
                        {"method": "PUT", "path": SETTINGS, "status": 200},
                        {"method": "PUT", "path": KEY_PATH, "status": 200}]
                    assert not forbidden
                    assert errors == ["Failed to load resource: the server responded with a status of 409 (Conflict)"] * 2, errors
        finally:
            if (work / JOURNAL).is_file():
                shutil.copyfile(work / JOURNAL, ARTIFACTS / JOURNAL)
    print("UI14 PASS: active settings/key rejected, Free-only provider traffic, round preserved, idle updates accepted", flush=True)


def main():
    assert (DIST / "index.html").is_file()
    with sync_playwright() as playwright:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            browser_case(browser)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
