"""UI-11: real persisted native history, then read-only Chromium reloads."""
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from serve_ui04 import JOURNAL, TITLE_MODEL
from serve_ui07 import CASES, CHAIRMAN, FINAL, MAPPING, MODELS, SCENARIO
from serve_ui11 import PLAN
from ui01 import BASE, DIST, EXISTING, ROOT, is_response, isolated_server, json_response, observed_browser
from ui04 import progress_locator
from ui07 import assert_chairman_result, assert_final_display, assert_peer_result, assert_warnings, configure, expected_progress

ARTIFACTS = ROOT / "browser-artifacts" / "ui11" / os.getenv("UI11_RUN", "normal")
QUESTIONS = ("UI11: Preserve the archive? Türkçe ✓", "UI11: Is verification sufficient?", "UI11: Restore the other backup?")
TITLES = ("Archive history", "Other backup history")
CORRUPT = "c9ca3ea7-f9e1-4b55-81c9-c90c831bc113.json"
CORRUPT_BYTES = b'{"broken":'


def snapshot(data):
    return {p.name: p.read_bytes() for p in data.iterdir()}


def decode_round(response, case, title):
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    progress = expected_progress(case)
    types = ["engine_progress"] * len(progress) + ["stage1_complete", "stage2_complete", "stage3_complete", "complete"]
    if title is not None:
        types.append("title_complete")
    assert [event["type"] for event in events] == types, "UI11: real SSE sequence mismatch"
    actual_progress = [e["data"] for e in events[:len(progress)]]
    assert [(e["stage"], e["state"]) for e in actual_progress] == progress
    stage1, stage2, stage3, complete = events[len(progress):len(progress) + 4]
    metadata = complete["metadata"]
    assert stage2["metadata"] == stage3["metadata"] == metadata
    assert metadata["ui_runtime_progress"] == actual_progress
    if title is not None:
        assert events[-1]["data"] == {"title": title}
    return {"stage1": stage1["data"], "stage2": stage2["data"], "stage3": stage3["data"], "metadata": metadata}


def expected_query(question, previous):
    if not previous["messages"]:
        return question
    assert previous["messages"] == [
        {"role": "user", "content": QUESTIONS[0]}, previous["messages"][1]]
    return ("Conversation background from prior completed Council rounds (not the current request):\n\n"
            "Previous user question: " + json.dumps(QUESTIONS[0], ensure_ascii=False)
            + "\nPrevious Council final answer: " + json.dumps(FINAL, ensure_ascii=False)
            + "\n\nCurrent user question for this Council round:\n" + question)


def generate(client, data, work, output, record, question, case, title):
    detail = f"/api/conversations/{record['id']}"
    path = data / f"{record['id']}.json"
    others = {name: raw for name, raw in snapshot(data).items() if name != path.name}
    first = not record["messages"]
    (work / SCENARIO).write_text(case.name, encoding="utf-8")
    (work / PLAN).write_text(json.dumps({"query": expected_query(question, record), "title": title}), encoding="utf-8")
    (work / MAPPING).unlink(missing_ok=True)
    (work / JOURNAL).unlink(missing_ok=True)
    response = client.post(detail + "/message/stream", json={"content": question, "verdict_type": case.mode,
                            "include_dissent": case.dissent}, timeout=45)
    payload = decode_round(response, case, title if first else None)
    assert_peer_result(case, payload, work)
    assert_chairman_result(case, payload)
    expected = {**record, "title": title, "messages": record["messages"] + [
        {"role": "user", "content": question}, {"role": "assistant", **payload}]}
    assert json_response(client.get(detail)) == expected and json.loads(path.read_bytes()) == expected, "UI11: persisted history mismatch"
    assert {name: raw for name, raw in snapshot(data).items() if name != path.name} == others, "UI11: foreign history changed"
    provider = [json.loads(line) for line in (work / JOURNAL).read_text(encoding="utf-8").splitlines()]
    calls = [(stage, model, 200) for stage in ("stage1", "stage2") for model in MODELS] + [("stage3", CHAIRMAN, 200)]
    if first:
        calls.append(("title", TITLE_MODEL, 200))
    assert Counter((r["stage"], r["model"], r["status"]) for r in provider) == Counter(calls)
    assert [r["stage"] for r in provider if r["stage"] != "title"] == ["stage1"] * 4 + ["stage2"] * 4 + ["stage3"]
    case_dir = output / case.name
    case_dir.mkdir(exist_ok=True)
    (case_dir / "sse.txt").write_text(response.text, encoding="utf-8")
    (case_dir / "record.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in (JOURNAL, MAPPING):
        shutil.copyfile(work / name, case_dir / name)
    return expected


def verify_stage_history(message, payload):
    stage1 = message.locator(".stage1")
    expect(stage1.locator(".tabs button")).to_have_count(len(payload["stage1"]))
    for row in payload["stage1"]:
        stage1.get_by_role("button", name=row["model"].split("/")[-1], exact=True).click()
        expect(stage1.locator(".model-name")).to_have_text(row["model"])
        expect(stage1.locator(".response-text"), "UI11: historical model response mismatch").to_have_text(row["response"])
    stage2 = message.locator(".stage2")
    expect(stage2.locator(".tabs button")).to_have_count(len(payload["stage2"]))
    mapping = payload["metadata"]["label_to_model"]
    for row in payload["stage2"]:
        stage2.get_by_role("button", name=row["model"].split("/")[-1], exact=True).click()
        expect(stage2.locator(".ranking-model")).to_have_text(row["model"])
        prose, raw = row["ranking"].split("\n```json\n")
        expect(stage2.locator(".ranking-content p")).to_have_text(prose)
        for label, item in mapping.items():
            raw = raw.replace(label, item["model"].split("/")[-1])
        rendered = stage2.locator(".ranking-content pre code")
        expect(rendered).to_be_visible()
        assert json.loads(rendered.inner_text().replace("**", "")) == json.loads(raw.removesuffix("\n```")), "UI11: historical peer review mismatch"


def verify_history(page, record, cases):
    questions = [m["content"] for m in record["messages"] if m["role"] == "user"]
    expect(page.locator(".user-message .markdown-content"), "UI11: historical questions missing or wrong").to_have_text(questions)
    assistants = [m for m in record["messages"] if m["role"] == "assistant"]
    messages = page.locator(".assistant-message")
    expect(messages, "UI11: historical assistant count mismatch").to_have_count(len(assistants))
    for message, payload, case in zip(messages.all(), assistants, cases):
        if case.error_status:
            expect(message.locator(".stage3 .final-text, .verdict-summary"), "UI11: historical failure shown as success").to_have_count(0)
            expect(message.locator(".stage3-error p"), "UI11: historical error missing").to_have_text(payload["stage3"]["response"])
        elif case.peer == "partial":
            expect(message.locator(".stage2 [role=alert]"), "UI11: historical partial warning missing").to_be_visible()
        assert_final_display(message, case, payload)
        assert_warnings(message, case)
        verify_stage_history(message, payload)
        expect(message.locator(".engine-progress-event")).to_have_count(len(expected_progress(case)))
        for stage, state in expected_progress(case):
            expect(progress_locator(message, stage, state)).to_be_visible()
    expect(page.locator(".loading-indicator, .stage-loading, .council-error")).to_have_count(0)
    expect(page.locator(".message-input")).to_be_editable()
    expect(page.get_by_role("button", name=f"{record['title']} {len(record['messages'])} messages", exact=True)).to_have_attribute("aria-current", "page")


def open_history(page, record, cases):
    detail = f"/api/conversations/{record['id']}"
    with page.expect_response(lambda r: is_response(r, "GET", detail)) as loaded:
        page.get_by_role("button", name=f"{record['title']} {len(record['messages'])} messages", exact=True).click()
    assert loaded.value.status == 200 and loaded.value.json() == record, "UI11: loaded API history mismatch"
    verify_history(page, record, cases)


def history_reads(page, client, data, work, output, records, cases, network):
    before = snapshot(data)
    journal = (work / JOURNAL).read_bytes()
    expected_list = sorted([{"id": r["id"], "created_at": r["created_at"], "title": r["title"],
                             "message_count": len(r["messages"])} for r in records], key=lambda r: r["created_at"], reverse=True)
    for cycle, order in enumerate(((0, 1, 2, 0), (1, 0, 2, 1))):
        with page.expect_response(lambda r: is_response(r, "GET", "/api/conversations")) as listed:
            if cycle == 0:
                assert page.goto(BASE + "/app/", wait_until="domcontentloaded").status == 200
            else:
                page.reload(wait_until="domcontentloaded")
        assert listed.value.status == 200 and listed.value.json() == expected_list
        expect(page.locator(".conversation-select")).to_have_count(len(records))
        assert snapshot(data) == before, "UI11: read changed or lost stored bytes"
        for index in order:
            open_history(page, records[index], cases[index])
            assert snapshot(data) == before, "UI11: read changed or lost stored bytes"
        page.screenshot(path=str(output / f"reload-{cycle}.png"), full_page=True)
    assert json_response(client.get("/api/conversations")) == expected_list
    for record in records:
        assert json_response(client.get(f"/api/conversations/{record['id']}")) == record
    assert snapshot(data) == before and before[CORRUPT] == CORRUPT_BYTES, "UI11: read changed or lost stored bytes"
    assert (work / JOURNAL).read_bytes() == journal, "UI11: history read invoked provider"
    assert all(r["method"] == "GET" for r in network), "UI11: history read made a write request"
    (output / "history-list.json").write_text(json.dumps(expected_list, indent=2), encoding="utf-8")


def main():
    assert (DIST / "index.html").is_file(), "UI11: production frontend build is missing"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="council-ui11-") as directory, sync_playwright() as playwright:
        work = Path(directory)
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            with isolated_server(work, ARTIFACTS, server_script="serve_ui11.py") as (client, data):
                configure(client)
                a = json_response(client.post("/api/conversations", json={}))
                b = json_response(client.post("/api/conversations", json={}))
                full, partial, failed = (CASES[name] for name in ("synthesis", "partial", "empty"))
                a = generate(client, data, work, ARTIFACTS, a, QUESTIONS[0], full, TITLES[0])
                a = generate(client, data, work, ARTIFACTS, a, QUESTIONS[1], partial, TITLES[0])
                b = generate(client, data, work, ARTIFACTS, b, QUESTIONS[2], failed, TITLES[1])
                (data / f"{EXISTING['id']}.json").write_text(json.dumps(EXISTING), encoding="utf-8")
                (data / CORRUPT).write_bytes(CORRUPT_BYTES)
                with observed_browser(browser, ARTIFACTS) as (page, network, errors, forbidden):
                    history_reads(page, client, data, work, ARTIFACTS, (a, b, EXISTING), ((full, partial), (failed,), ()), network)
                    assert not errors and not forbidden, "UI11: browser errors or external traffic"
        finally:
            browser.close()
    print("UI11 PASS: three native rounds, two reload cycles, eight history selections, preserved bytes", flush=True)


if __name__ == "__main__":
    main()
