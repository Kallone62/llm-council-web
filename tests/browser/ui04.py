"""UI-04: actual Chromium -> FastAPI -> native Council -> controlled HTTP -> disk."""
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from serve_ui04 import MODELS, CHAIRMAN, TITLE_MODEL, KEY, QUESTION, TITLE, FINAL, LABELS, REVIEW, REVIEW_INTRO, STAGES, JOURNAL, candidate
from ui01 import BASE, DIST, ROOT, EXISTING, isolated_server, observed_browser, is_response, json_response

ARTIFACTS = ROOT / "browser-artifacts" / "ui04" / os.getenv("UI04_RUN", "normal")
HEADINGS = ("Stage 1: Individual Responses", "Stage 2: Peer Rankings", "Stage 3: Final Council Answer")
PROGRESS = {
    "stage1": "Stage 1 · Individual responses",
    "stage1_5": "Stage 1.5 · Style normalization",
    "stage2": "Stage 2 · Peer review",
    "stage3": "Stage 3 · Chairman synthesis",
}
EXPECTED_PROGRESS = [(stage, state) for stage in PROGRESS for state in ("started", "completed")]


def configure(client):
    settings = json_response(client.put("/api/settings", json={"profile": "quick"}))
    assert settings["profile"] == "quick" and settings["models"] == MODELS
    assert settings["resolved_chairman"] == CHAIRMAN and settings["use_profile_models"] is True
    assert json_response(client.put("/api/settings/openrouter-key", json={"api_key": KEY})) == {"configured": True}
    assert json_response(client.get("/api/settings")) == {**settings, "api_key_configured": True}


def progress_locator(page, stage, state):
    status = "running" if state == "started" else state
    return page.locator(".engine-progress-event").filter(has_text=f"{PROGRESS[stage]}: {status}")


def assert_live(page, work, entry):
    for stage in STAGES:
        expect(progress_locator(page, stage, "started"), f"UI04: live {stage} progress missing").to_be_visible()
        expect(progress_locator(page, stage, "completed"), "UI04: premature completion").to_have_count(0)
        for previous in STAGES[:STAGES.index(stage)]:
            expect(progress_locator(page, previous, "completed")).to_be_visible()
        expect(entry, "UI04: loading did not start").to_be_disabled()
        expect(page.get_by_text("Council engine running…", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Send", exact=True)).to_be_disabled()
        expect(page.get_by_role("heading", name=HEADINGS[2], exact=True)).to_have_count(0)
        (work / f"release-{stage}").touch()


def decode_stream(response):
    assert response.status == 200 and response.headers["content-type"].startswith("text/event-stream")
    body = response.body().decode("utf-8")
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert [event["type"] for event in events] == ["engine_progress"] * len(EXPECTED_PROGRESS) + [
        "stage1_complete", "stage2_complete", "stage3_complete", "complete", "title_complete"], "UI04: SSE event sequence mismatch"
    progress = [event["data"] for event in events[:-5]]
    assert [(event["stage"], event["state"]) for event in progress] == EXPECTED_PROGRESS
    elapsed = [event["elapsed_seconds"] for event in progress]
    assert elapsed == sorted(elapsed) and elapsed[0] >= 0
    assert events[-1]["data"] == {"title": TITLE}
    metadata = events[-2]["metadata"]
    assert events[-4]["metadata"] == events[-3]["metadata"] == metadata
    assert metadata["ui_runtime_progress"] == progress
    return {"stage1": events[-5]["data"], "stage2": events[-4]["data"], "stage3": events[-3]["data"], "metadata": metadata}


def assert_result(payload):
    answers, reviews, final, metadata = (payload[key] for key in ("stage1", "stage2", "stage3", "metadata"))
    assert len(answers) == len(reviews) == len(MODELS)
    assert {row["model"]: row["response"] for row in answers} == {model: candidate(model) for model in MODELS}
    assert {row["model"] for row in reviews} == set(MODELS)
    assert all(row["ranking"] == REVIEW and row["parsed_ranking"]["ranking"] == LABELS for row in reviews)
    assert final["model"] == CHAIRMAN and final["response"] == FINAL and not final.get("error_status")
    assert metadata.get("status") not in ("failed", "partial") and not metadata.get("degraded_mode")
    assert metadata["peer_review"]["state"] == "completed"
    assert metadata["config"]["council_size"] == metadata["config"]["responses_received"] == len(MODELS)


def assert_provider(work):
    records = [json.loads(line) for line in (work / JOURNAL).read_text(encoding="utf-8").splitlines()]
    expected = [("title", TITLE_MODEL), *( (stage, model) for stage in STAGES[:2] for model in MODELS), ("stage3", CHAIRMAN)]
    assert Counter((row["phase"], row["stage"], row["model"]) for row in records) == Counter(
        (phase, stage, model) for phase in ("request", "response") for stage, model in expected), "UI04: unexpected provider traffic"
    requests = [row["stage"] for row in records if row["phase"] == "request" and row["stage"] != "title"]
    assert requests == ["stage1"] * len(MODELS) + ["stage2"] * len(MODELS) + ["stage3"]


def assert_display(page, payload):
    for heading in HEADINGS:
        expect(page.get_by_role("heading", name=heading, exact=True), "UI04: stage result missing").to_be_visible()
    expect(page.locator(".stage1 .response-text")).to_have_text(payload["stage1"][0]["response"])
    expect(page.locator(".stage1 .model-name")).to_have_text(payload["stage1"][0]["model"])
    expect(page.locator(".stage2 .ranking-content")).to_contain_text(REVIEW_INTRO)
    expect(page.locator(".stage2 .ranking-model")).to_have_text(payload["stage2"][0]["model"])
    expect(page.locator(".stage3 .final-text"), "UI04: final answer missing").to_have_text(FINAL)
    expect(page.locator(".stage3 .chairman-label")).to_have_text("Chairman: " + CHAIRMAN.split("/")[1])
    expect(page.locator(".engine-progress-event")).to_have_count(len(EXPECTED_PROGRESS))
    for stage, state in EXPECTED_PROGRESS:
        expect(progress_locator(page, stage, state)).to_be_visible()
    expect(page.locator(".loading-indicator, .stage-loading, .council-error, .stage3-error")).to_have_count(0)


def check_round(page, client, data, work, output=ARTIFACTS):
    configure(client)
    with page.expect_response(lambda r: is_response(r, "GET", "/api/settings")) as loaded:
        response = page.goto(BASE + "/app/", wait_until="domcontentloaded")
    assert response and response.status == 200 and loaded.value.json()["api_key_configured"] is True
    with page.expect_response(lambda r: is_response(r, "POST", "/api/conversations")) as created:
        page.get_by_role("button", name="+ New Conversation", exact=True).click()
    assert created.value.status == 200
    cid = created.value.json()["id"]
    detail = f"/api/conversations/{cid}"
    entry = page.get_by_placeholder("Ask a question or continue this conversation... (Shift+Enter for new line)")
    expect(entry).to_be_editable()
    entry.fill(QUESTION)
    with page.expect_response(lambda r: is_response(r, "POST", detail + "/message/stream")) as streamed:
        page.get_by_role("button", name="Send", exact=True).click()
    expect(page.get_by_text(QUESTION, exact=True)).to_be_visible()
    assert streamed.value.request.post_data_json == {"content": QUESTION, "verdict_type": "synthesis", "include_dissent": False}
    assert_live(page, work, entry)
    expect(entry, "UI04: loading did not end").to_be_editable()
    payload = decode_stream(streamed.value)
    assert_result(payload)
    record = json_response(client.get(detail))
    expected_messages = [{"role": "user", "content": QUESTION}, {"role": "assistant", **payload}]
    disk = json.loads((data / f"{cid}.json").read_text(encoding="utf-8"))
    assert record == disk and record["messages"] == expected_messages and record["title"] == TITLE, "UI04: persisted result mismatch"
    assert_display(page, payload)
    expect(page.get_by_role("button", name=f"{TITLE} 2 messages", exact=True)).to_be_visible()
    assert_provider(work)
    (output / "sse.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return detail


def browser_case(browser, *, output=ARTIFACTS, after_round=None):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="council-ui04-") as directory:
        work = Path(directory)
        try:
            with isolated_server(work, output, server_script="serve_ui04.py") as (client, data):
                old = data / f"{EXISTING['id']}.json"
                old.write_text(json.dumps(EXISTING), encoding="utf-8")
                before = old.read_bytes()
                with observed_browser(browser, output) as (page, network, errors, forbidden):
                    detail = check_round(page, client, data, work, output)
                    if after_round is not None:
                        after_round(page, client, data, work, detail, output)
                    assert old.read_bytes() == before and len(list(data.glob("*.json"))) == 2
                    assert [row for row in network if row["method"] == "POST"] == [
                        {"method": "POST", "path": "/api/conversations", "status": 200},
                        {"method": "POST", "path": detail + "/message/stream", "status": 200}]
                    assert not forbidden and not errors, "UI04: browser errors or external traffic"
        finally:
            if (work / JOURNAL).is_file():
                shutil.copyfile(work / JOURNAL, output / JOURNAL)
    print("UI04 PASS: real three-stage stream, live progress, loading, UI/API/disk", flush=True)


def main(*, output=ARTIFACTS, after_round=None):
    assert (DIST / "index.html").is_file(), "UI04: production frontend build is missing"
    with sync_playwright() as playwright:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            browser_case(browser, output=output, after_round=after_round)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
