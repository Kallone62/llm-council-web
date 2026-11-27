"""UI-08 extension: successful rolling memory, reload and cached summary reuse."""
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from serve_ui04 import CHAIRMAN, FINAL, MODELS, TITLE_MODEL
from serve_ui08_memory import JOURNAL, LONG_ANSWER, LONG_QUESTION, NEW_MEMORY, OLD_MEMORY, QUESTIONS, RELEASE, ROUND, SUMMARY
from ui01 import BASE, DIST, EXISTING, ROOT, isolated_server, is_response, json_response, observed_browser
from ui04 import EXPECTED_PROGRESS, assert_result, configure
from ui08 import select

ARTIFACTS = ROOT / "browser-artifacts" / "ui08" / os.getenv("UI08_MEMORY_RUN", "memory-normal") / "rolling-memory"
RECORD = {"id": "438d06d4-2aa5-4215-9eb4-0a2d3bc949d7", "created_at": "2025-01-02T00:00:00+00:00",
          "title": "Memory archive decision", "messages": [
    {"role": "user", "content": "Which original archive?"},
    {"role": "assistant", "stage1": [], "stage2": [], "stage3": {"model": CHAIRMAN, "response": "Keep BLUE."}, "metadata": {}},
    {"role": "user", "content": "FAILED_RED_QUESTION"},
    {"role": "assistant", "stage1": [], "stage2": [], "stage3": {"model": CHAIRMAN, "response": "FAILED_RED_RESPONSE", "error_status": "empty_synthesis"}, "metadata": {}},
    {"role": "user", "content": LONG_QUESTION},
    {"role": "assistant", "stage1": [], "stage2": [], "stage3": {"model": CHAIRMAN, "response": LONG_ANSWER}, "metadata": {}},
], "context_memory": OLD_MEMORY}


def expected_query(round_id):
    history = "Earlier conversation memory (AI-generated summary; consult the actual current question if there is any conflict):\n" + SUMMARY
    if round_id == "reuse":
        history += ("\n\nPrevious user question: " + json.dumps(QUESTIONS["summary"], ensure_ascii=False)
                    + "\nPrevious Council final answer: " + json.dumps(FINAL, ensure_ascii=False))
    return ("Conversation background from prior completed Council rounds (not the current request):\n\n" + history
            + "\n\nCurrent user question for this Council round:\n" + QUESTIONS[round_id])


def read_journal(work):
    path = work / JOURNAL
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def assert_provider(round_id, rows):
    if round_id == "reuse":
        assert not any(row["stage"] == "memory" for row in rows), "UI08-memory: cached summary invoked provider"
    expected = [(stage, model) for stage in ("stage1", "stage2") for model in MODELS] + [("stage3", CHAIRMAN)]
    if round_id == "summary":
        expected.insert(0, ("memory", TITLE_MODEL))
    assert Counter((row["stage"], row["model"]) for row in rows) == Counter(expected), "UI08-memory: provider traffic mismatch"
    assert [row["stage"] for row in rows] == [stage for stage, _ in expected]
    assert all(row["round"] == round_id for row in rows)
    query = expected_query(round_id)
    for row in rows:
        stage, prompt = row["stage"], row["prompt"]
        if stage == "memory":
            assert json.loads(prompt) == {"previous_memory": OLD_MEMORY["summary"],
                "completed_exchanges_since_memory": [{"user_question": LONG_QUESTION, "chairman_final_answer": LONG_ANSWER}]}, "UI08-memory: wrong summary input"
        elif stage == "stage1":
            assert prompt == query, "UI08-memory: effective query mismatch"
        elif stage == "stage2":
            assert f"<question>{query}</question>" in prompt, "UI08-memory: review context mismatch"
        else:
            assert f"Original Question: {query}\n\nSTAGE 1 - Individual Responses:" in prompt, "UI08-memory: Chairman context mismatch"


def decode_stream(response, round_id):
    assert response.status == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.body().decode("utf-8").splitlines() if line.startswith("data: ")]
    states = ([("memory", "started"), ("memory", "completed")] if round_id == "summary" else []) + EXPECTED_PROGRESS
    assert [e["type"] for e in events] == ["engine_progress"] * len(states) + ["stage1_complete", "stage2_complete", "stage3_complete", "complete"], "UI08-memory: SSE sequence mismatch"
    progress = [e["data"] for e in events[:-4]]
    assert [(e["stage"], e["state"]) for e in progress] == states
    metadata = events[-1]["metadata"]
    assert events[-3]["metadata"] == events[-2]["metadata"] == metadata and metadata["ui_runtime_progress"] == progress
    return {"stage1": events[-4]["data"], "stage2": events[-3]["data"], "stage3": events[-2]["data"], "metadata": metadata}


def public(record):
    # The Conversation response model deliberately omits internal context_memory.
    return {key: value for key, value in record.items() if key != "context_memory"}


def memory_progress(page, state):
    return page.locator(".assistant-message").last.locator(".engine-progress-event").filter(has_text=f"Conversation memory · AI summary: {state}")


def submit(page, client, data, work, before, round_id):
    detail = f"/api/conversations/{before['id']}"
    path = data / f"{before['id']}.json"
    old_bytes = path.read_bytes()
    prior_rows = read_journal(work)
    (work / ROUND).write_text(round_id, encoding="utf-8")
    entry = page.get_by_placeholder("Ask a question or continue this conversation... (Shift+Enter for new line)")
    expect(entry).to_be_editable()
    entry.fill(QUESTIONS[round_id])
    with page.expect_response(lambda r: is_response(r, "POST", detail + "/message/stream")) as sent:
        page.get_by_role("button", name="Send", exact=True).click()
    assert sent.value.request.post_data_json == {"content": QUESTIONS[round_id], "verdict_type": "synthesis", "include_dissent": False}
    if round_id == "summary":
        expect(memory_progress(page, "running"), "UI08-memory: running summary not visible").to_be_visible()
        expect(memory_progress(page, "completed")).to_have_count(0)
        expect(page.locator(".loading-indicator")).to_be_visible()
        expect(entry).to_be_disabled()
        expect(page.locator(".assistant-message").last.locator(".stage3")).to_have_count(0)
        assert path.read_bytes() == old_bytes
        (work / RELEASE).touch()
    expect(entry, "UI08-memory: loading did not end").to_be_editable()
    rows = read_journal(work)
    assert rows[:len(prior_rows)] == prior_rows
    assert_provider(round_id, rows[len(prior_rows):])
    payload = decode_stream(sent.value, round_id)
    assert_result(payload)
    actual = json.loads(path.read_bytes())
    assert actual.get("context_memory") == NEW_MEMORY, "UI08-memory: persisted summary mismatch"
    assert actual == {**before, "context_memory": NEW_MEMORY, "messages": before["messages"] + [
        {"role": "user", "content": QUESTIONS[round_id]}, {"role": "assistant", **payload}]}, "UI08-memory: persisted history mismatch"
    assert json_response(client.get(detail)) == public(actual)
    expect(page.locator(".assistant-message").last.locator(".stage3 .final-text")).to_have_text(FINAL)
    expect(page.locator(".loading-indicator, .stage-loading, .council-error")).to_have_count(0)
    if round_id == "summary":
        expect(memory_progress(page, "completed"), "UI08-memory: completed summary not visible").to_be_visible()
        expect(memory_progress(page, "failed")).to_have_count(0)
    else:
        expect(page.locator(".assistant-message").last.locator(".engine-progress-event").filter(has_text="Conversation memory")).to_have_count(0)
    return actual


def reload_history(page, client, data, work, record):
    path = data / f"{record['id']}.json"
    before, rows = path.read_bytes(), read_journal(work)
    with page.expect_response(lambda r: is_response(r, "GET", "/api/conversations")):
        page.reload(wait_until="domcontentloaded")
    select(page, public(record))
    expect(memory_progress(page, "completed"), "UI08-memory: completed summary lost after reload").to_be_visible()
    assert json_response(client.get(f"/api/conversations/{record['id']}")) == public(record)
    assert path.read_bytes() == before and read_journal(work) == rows


def main():
    assert (DIST / "index.html").is_file(), "UI08-memory: production frontend build is missing"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright, tempfile.TemporaryDirectory(prefix="council-ui08-memory-") as directory:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        work = Path(directory)
        try:
            with isolated_server(work, ARTIFACTS, server_script="serve_ui08_memory.py") as (client, data):
                configure(client)
                for record in (RECORD, EXISTING):
                    (data / f"{record['id']}.json").write_text(json.dumps(record), encoding="utf-8")
                other = data / f"{EXISTING['id']}.json"
                original = other.read_bytes()
                with observed_browser(browser, ARTIFACTS) as (page, network, errors, forbidden):
                    with page.expect_response(lambda r: is_response(r, "GET", "/api/settings")) as loaded:
                        assert page.goto(BASE + "/app/", wait_until="domcontentloaded").status == 200
                    assert loaded.value.json()["api_key_configured"] is True
                    select(page, public(RECORD))
                    updated = submit(page, client, data, work, RECORD, "summary")
                    reload_history(page, client, data, work, updated)
                    final = submit(page, client, data, work, updated, "reuse")
                    assert other.read_bytes() == original and len(list(data.glob("*.json"))) == 2
                    assert [r for r in network if r["method"] == "POST"] == [
                        {"method": "POST", "path": f"/api/conversations/{RECORD['id']}/message/stream", "status": 200}] * 2
                    assert not errors and not forbidden, "UI08-memory: unexpected browser errors or external traffic"
                    (ARTIFACTS / "record.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
        finally:
            browser.close()
            if (work / JOURNAL).is_file():
                shutil.copyfile(work / JOURNAL, ARTIFACTS / JOURNAL)
    print("UI08-memory PASS: summary, reload and provider-free cached summary reuse", flush=True)


if __name__ == "__main__":
    main()
