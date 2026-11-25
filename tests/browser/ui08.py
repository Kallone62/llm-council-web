"""UI-08: follow-up context, immutable raw history and conversation isolation."""
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from serve_ui04 import CHAIRMAN, JOURNAL, LABELS, MODELS, REVIEW, TITLE_MODEL
from serve_ui08 import ERROR, FINALS, QUESTIONS, ROUND_FILE, TITLES, candidate
from ui01 import BASE, DIST, ROOT, is_response, isolated_server, json_response, observed_browser
from ui04 import EXPECTED_PROGRESS, configure

ARTIFACTS = ROOT / "browser-artifacts" / "ui08" / os.getenv("UI08_RUN", "normal")
ORDER = ("a-first", "b-first", "a-failed", "a-follow", "b-follow")


def expected_query(round_id):
    # Independent oracle: no calls to production context helpers or stored result
    # construction. Both follow-ups have identical current question text.
    question = QUESTIONS[round_id]
    if round_id.endswith("-first"):
        return question
    first = round_id[0] + "-first"
    return ("Conversation background from prior completed Council rounds (not the current request):\n\n"
            "Previous user question: " + json.dumps(QUESTIONS[first], ensure_ascii=False)
            + "\nPrevious Council final answer: " + json.dumps(FINALS[first], ensure_ascii=False)
            + "\n\nCurrent user question for this Council round:\n" + question)


def assert_prompts(round_id, rows):
    expected = [(stage, model) for stage in ("stage1", "stage2") for model in MODELS] + [("stage3", CHAIRMAN)]
    if round_id.endswith("-first"):
        expected.append(("title", TITLE_MODEL))
    assert Counter((row["stage"], row["model"]) for row in rows) == Counter(expected), "UI08: provider traffic mismatch"
    assert all(row["round"] == round_id for row in rows)
    assert [row["stage"] for row in rows if row["stage"] != "title"] == ["stage1"] * 3 + ["stage2"] * 3 + ["stage3"]
    query = expected_query(round_id)
    for row in rows:
        prompt, stage = row["prompt"], row["stage"]
        if stage == "stage1":
            assert prompt == query, f"UI08: effective context mismatch ({round_id})"
        elif stage == "stage2":
            assert f"<question>{query}</question>" in prompt, "UI08: review context mismatch"
        elif stage == "stage3":
            assert f"Original Question: {query}\n\nSTAGE 1 - Individual Responses:" in prompt, "UI08: chairman context mismatch"
        else:
            assert f"Question: {QUESTIONS[round_id]}" in prompt, "UI08: title context mismatch"


def decode_stream(response, round_id):
    assert response.status == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.body().decode("utf-8").splitlines() if line.startswith("data: ")]
    expected = ["engine_progress"] * len(EXPECTED_PROGRESS) + ["stage1_complete", "stage2_complete", "stage3_complete", "complete"]
    if round_id.endswith("-first"):
        expected.append("title_complete")
    assert [event["type"] for event in events] == expected, "UI08: SSE sequence mismatch"
    progress = [event["data"] for event in events[:len(EXPECTED_PROGRESS)]]
    states = list(EXPECTED_PROGRESS)
    if round_id == "a-failed":
        states[-1] = ("stage3", "failed")
        assert progress[-1]["error_status"] == "empty_synthesis"
    assert [(event["stage"], event["state"]) for event in progress] == states
    elapsed = [event["elapsed_seconds"] for event in progress]
    assert elapsed == sorted(elapsed) and elapsed[0] >= 0
    first, second, third, complete = events[len(EXPECTED_PROGRESS):len(EXPECTED_PROGRESS) + 4]
    metadata = complete["metadata"]
    assert second["metadata"] == third["metadata"] == metadata and metadata["ui_runtime_progress"] == progress
    if round_id.endswith("-first"):
        assert events[-1]["data"] == {"title": TITLES[round_id[0]]}
    return {"stage1": first["data"], "stage2": second["data"], "stage3": third["data"], "metadata": metadata}


def assert_result(round_id, payload):
    assert len(payload["stage1"]) == len(payload["stage2"]) == len(MODELS)
    assert {r["model"]: r["response"] for r in payload["stage1"]} == {m: candidate(round_id, m) for m in MODELS}
    assert {r["model"] for r in payload["stage2"]} == set(MODELS)
    assert all(r["ranking"] == REVIEW and r["parsed_ranking"]["ranking"] == LABELS for r in payload["stage2"])
    final, metadata = payload["stage3"], payload["metadata"]
    assert final["model"] == CHAIRMAN and metadata["peer_review"]["state"] == "completed"
    assert metadata["peer_review"]["effective_votes"] == 6
    if round_id == "a-failed":
        assert final["response"] == ERROR and final["error_status"] == "empty_synthesis"
        assert metadata["status"] == "failed" and not metadata.get("verdict") and metadata.get("quality_metrics") is None
    else:
        assert final["response"] == FINALS[round_id] and not final.get("error_status")
        assert metadata.get("status") not in ("failed", "partial")


def assert_history(before, actual, round_id, payload):
    prefix = before["messages"]
    assert actual["messages"][:len(prefix)] == prefix, "UI08: prior raw history changed"
    assert actual == {**before, "title": TITLES[round_id[0]], "messages": prefix + [
        {"role": "user", "content": QUESTIONS[round_id]}, {"role": "assistant", **payload}]}, "UI08: persisted round mismatch"
    assert "context_memory" not in actual, "UI08: unexpected summary for short history"


def assert_visible_history(page, record):
    questions = [m["content"] for m in record["messages"] if m["role"] == "user"]
    expect(page.locator(".user-message .markdown-content"), "UI08: displayed user history mismatch").to_have_text(questions)
    messages = page.locator(".assistant-message")
    answers = [m["stage3"] for m in record["messages"] if m["role"] == "assistant"]
    expect(messages).to_have_count(len(answers))
    for message, answer in zip(messages.all(), answers):
        if answer.get("error_status"):
            expect(message.locator(".stage3-error p")).to_have_text(answer["response"])
            expect(message.locator(".stage3 .final-text")).to_have_count(0)
        else:
            expect(message.locator(".stage3 .final-text"), "UI08: displayed final history mismatch").to_have_text(answer["response"])
    sidebar = page.get_by_role("button", name=f"{record['title']} {len(record['messages'])} messages", exact=True)
    expect(sidebar).to_have_attribute("aria-current", "page")


def create(page):
    with page.expect_response(lambda r: is_response(r, "POST", "/api/conversations")) as created:
        page.get_by_role("button", name="+ New Conversation", exact=True).click()
    assert created.value.status == 200
    return created.value.json()["id"]


def select(page, record):
    detail = f"/api/conversations/{record['id']}"
    with page.expect_response(lambda r: is_response(r, "GET", detail)) as loaded:
        page.get_by_role("button", name=f"{record['title']} {len(record['messages'])} messages", exact=True).click()
    assert loaded.value.status == 200 and loaded.value.json() == record
    assert_visible_history(page, record)


def submit(page, cid, round_id):
    entry = page.get_by_placeholder("Ask a question or continue this conversation... (Shift+Enter for new line)")
    expect(entry).to_be_editable()
    entry.fill(QUESTIONS[round_id])
    detail = f"/api/conversations/{cid}"
    with page.expect_response(lambda r: is_response(r, "POST", detail + "/message/stream")) as streamed:
        page.get_by_role("button", name="Send", exact=True).click()
    assert streamed.value.request.post_data_json == {"content": QUESTIONS[round_id], "verdict_type": "synthesis", "include_dissent": False}
    payload = decode_stream(streamed.value, round_id)
    expect(entry, "UI08: loading did not end").to_be_editable()
    expect(page.locator(".loading-indicator, .stage-loading, .council-error")).to_have_count(0)
    return payload


def read_journal(work):
    path = work / JOURNAL
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def check_round(page, client, data, work, cid, round_id):
    before = json_response(client.get(f"/api/conversations/{cid}"))
    path = data / f"{cid}.json"
    assert json.loads(path.read_bytes()) == before
    others = {p.name: p.read_bytes() for p in data.glob("*.json") if p != path}
    journal_before = read_journal(work)
    (work / ROUND_FILE).write_text(round_id, encoding="utf-8")
    payload = submit(page, cid, round_id)
    assert_result(round_id, payload)
    actual = json_response(client.get(f"/api/conversations/{cid}"))
    assert json.loads(path.read_bytes()) == actual
    assert_history(before, actual, round_id, payload)
    assert all((data / name).read_bytes() == raw for name, raw in others.items()), "UI08: other conversation changed"
    assert {p.name for p in data.glob("*.json")} == set(others) | {path.name}
    journal = read_journal(work)
    assert journal[:len(journal_before)] == journal_before
    assert_prompts(round_id, journal[len(journal_before):])
    assert_visible_history(page, actual)
    (ARTIFACTS / f"{round_id}.json").write_text(json.dumps(actual, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"UI08 PASS: {round_id}", flush=True)
    return actual


def scenario(page, client, data, work, network):
    configure(client)
    with page.expect_response(lambda r: is_response(r, "GET", "/api/settings")) as loaded:
        response = page.goto(BASE + "/app/", wait_until="domcontentloaded")
    assert response and response.status == 200 and loaded.value.json()["api_key_configured"] is True
    records, posts = {}, []
    selected = None
    for round_id in ORDER:
        name = round_id[0]
        if round_id.endswith("-first"):
            cid = create(page)
            posts.append("/api/conversations")
        else:
            cid = records[name]["id"]
            if selected != name:
                select(page, records[name])
        records[name] = check_round(page, client, data, work, cid, round_id)
        selected = name
        posts.append(f"/api/conversations/{cid}/message/stream")
    assert records["a"]["id"] != records["b"]["id"]
    assert len(records["a"]["messages"]) == 6 and len(records["b"]["messages"]) == 4
    assert len(read_journal(work)) == 37
    for record in records.values():
        assert json_response(client.get(f"/api/conversations/{record['id']}")) == record
    select(page, records["a"])
    select(page, records["b"])
    assert [r for r in network if r["method"] == "POST"] == [{"method": "POST", "path": path, "status": 200} for path in posts]


def main():
    assert (DIST / "index.html").is_file(), "UI08: production frontend build is missing"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright, tempfile.TemporaryDirectory(prefix="council-ui08-") as directory:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        work = Path(directory)
        try:
            with isolated_server(work, ARTIFACTS, server_script="serve_ui08.py") as (client, data):
                with observed_browser(browser, ARTIFACTS) as (page, network, errors, forbidden):
                    scenario(page, client, data, work, network)
                    assert not forbidden and not errors, "UI08: browser errors or external traffic"
        finally:
            browser.close()
            if (work / JOURNAL).is_file():
                shutil.copyfile(work / JOURNAL, ARTIFACTS / JOURNAL)
    print("UI08 PASS: five rounds, exact context and preserved isolated histories", flush=True)


if __name__ == "__main__":
    main()
