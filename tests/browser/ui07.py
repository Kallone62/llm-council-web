"""UI-07 real browser / native Chairman results, verdict and failure presentation."""
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from serve_ui04 import JOURNAL, KEY, TITLE, TITLE_MODEL
from serve_ui07 import CASES, CHAIRMAN, DISSENT, MODELS, MAPPING, QUESTION, SCENARIO, VERDICT, candidate, review, ballot
from ui01 import BASE, DIST, ROOT, EXISTING, isolated_server, observed_browser, is_response, json_response
from ui04 import progress_locator

ARTIFACTS = ROOT / "browser-artifacts" / "ui07" / os.getenv("UI07_RUN", "normal")
NORMAL_HEADING = "Stage 3: Final Council Answer"
LIMITED_HEADING = "Stage 3: Chairman Answer (limited peer review)"
FAILED_HEADING = "Stage 3: Chairman unavailable"
UNAVAILABLE_HEADING = "Council unavailable: no model responses"
WARNINGS = {
    "partial": "Peer review is incomplete. Any Chairman answer is only partially peer-reviewed.",
    "failed": "No usable peer review was recorded. Any Chairman answer is not peer-validated.",
    "skipped": "Peer review was skipped because only one model responded. Any Chairman answer is not peer-validated.",
}


def configure(client):
    settings = json_response(client.put("/api/settings", json={"profile": "high"}))
    assert settings["profile"] == "high" and settings["models"] == MODELS
    assert settings["resolved_chairman"] == CHAIRMAN and settings["use_profile_models"] is True
    assert settings["exclude_self_votes"] is True
    assert json_response(client.put("/api/settings/openrouter-key", json={"api_key": KEY})) == {"configured": True}
    assert json_response(client.get("/api/settings")) == {**settings, "api_key_configured": True}


def expected_progress(case):
    if case.peer == "not_run":
        return [("stage1", "started"), ("stage1", "failed")]
    stages = [("stage1", "completed")]
    if case.peer != "skipped":
        stages += [("stage1_5", "completed"), ("stage2", case.peer)]
    stages.append(("stage3", "failed" if case.error_status else "completed"))
    return [(stage, state) for stage, end in stages for state in ("started", end)]


def decode_stream(response, case):
    assert response.status == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.body().decode("utf-8").splitlines() if line.startswith("data: ")]
    expected = expected_progress(case)
    assert [e["type"] for e in events] == ["engine_progress"] * len(expected) + [
        "stage1_complete", "stage2_complete", "stage3_complete", "complete", "title_complete"], "UI07: SSE sequence mismatch"
    progress = [e["data"] for e in events[:-5]]
    assert [(e["stage"], e["state"]) for e in progress] == expected, "UI07: progress state mismatch"
    elapsed = [e["elapsed_seconds"] for e in progress]
    assert elapsed == sorted(elapsed) and elapsed[0] >= 0
    if case.error_status and case.peer != "not_run":
        assert progress[-1]["error_status"] == case.error_status
    assert events[-1]["data"] == {"title": TITLE}
    metadata = events[-2]["metadata"]
    assert events[-4]["metadata"] == events[-3]["metadata"] == metadata
    assert metadata["ui_runtime_progress"] == progress
    return {"stage1": events[-5]["data"], "stage2": events[-4]["data"], "stage3": events[-3]["data"], "metadata": metadata}


def assert_peer_result(case, payload, work):
    metadata = payload["metadata"]
    reviews = payload["stage2"]
    peer = metadata["peer_review"]
    assert peer["state"] == case.peer
    assert peer["effective_votes"] == {"completed": 12, "partial": 9, "failed": 0, "skipped": 0, "not_run": 0}[case.peer]
    assert {r["model"]: r["response"] for r in payload["stage1"]} == {m: candidate(m) for m in case.members}
    if case.peer in ("skipped", "not_run"):
        assert not reviews and not (work / MAPPING).exists()
        return
    assert len(reviews) == len(MODELS) and {r["model"] for r in reviews} == set(MODELS)
    mapping = json.loads((work / MAPPING).read_text(encoding="utf-8"))
    assert {label: item["model"] for label, item in metadata["label_to_model"].items()} == mapping
    for row in reviews:
        expected = ballot(case, row["model"], mapping)
        assert row["ranking"] == review(case, row["model"], mapping)
        assert row["parsed_ranking"]["ranking"] == expected["ranking"]
        assert row["parsed_ranking"]["scores"] == expected["scores"]


def expected_verdict(case):
    return {**VERDICT, "verdict_type": case.effective_mode, "dissent": DISSENT if case.dissent else None,
            "deadlocked": case.effective_mode == "tie_breaker",
            "borda_spread": None if case.peer in ("failed", "skipped") else 0.0 if case.tied else 1.0}


def assert_chairman_result(case, payload):
    metadata, final = payload["metadata"], payload["stage3"]
    if case.peer != "not_run":
        config = metadata["config"]
        assert config["verdict_type"] == case.mode and config["effective_verdict_type"] == case.effective_mode
        assert config["include_dissent"] is case.dissent and config["deadlock_detected"] is case.tied
        assert config["responses_received"] == len(case.members) and config["council_size"] == len(MODELS)
    assert final["model"] == (CHAIRMAN if case.members else "error")
    if case.error_status:
        assert final.get("error_status") == case.error_status and metadata["status"] == "failed"
        assert not metadata.get("verdict") and metadata.get("quality_metrics") is None
        messages = {
            "empty_synthesis": "Chairman returned no usable final synthesis.",
            "auth_error": f"Error: Unable to generate final synthesis (auth_error: Authentication failed for {CHAIRMAN}: 401)",
            "no_responses": "No Council models returned a usable response. Check the OpenRouter model availability, account access and application logs.",
        }
        assert final["response"] == messages[case.error_status]
    else:
        assert not final.get("error_status") and final["response"] == case.final_content
        if case.peer == "completed":
            assert metadata.get("status") not in ("partial", "failed")
        else:
            assert metadata["status"] == "partial"
        if case.mode == "synthesis":
            assert "verdict" not in metadata
        else:
            assert metadata["verdict"] == expected_verdict(case)


def assert_provider(case, work):
    rows = [json.loads(line) for line in (work / JOURNAL).read_text(encoding="utf-8").splitlines()]
    expected = [("title", TITLE_MODEL, 200)] + [("stage1", m, 200 if m in case.members else 401) for m in MODELS]
    if len(case.members) == len(MODELS):
        expected.extend(("stage2", m, 200) for m in MODELS)
    if case.members:
        expected.append(("stage3", CHAIRMAN, 401 if case.failure == "auth" else 200))
    actual = [(r["stage"], r["model"], r["status"]) for r in rows]
    assert Counter(actual) == Counter(expected), "UI07: provider traffic mismatch"
    assert [s for s, *_ in actual if s != "title"] == [s for s, *_ in expected if s != "title"]


def assert_final_display(page, case, payload):
    stage = page.locator(".stage3")
    heading = UNAVAILABLE_HEADING if case.peer == "not_run" else FAILED_HEADING if case.error_status else LIMITED_HEADING if case.peer in WARNINGS else NORMAL_HEADING
    expect(stage.get_by_role("heading", name=heading, exact=True), "UI07: chairman heading mismatch").to_be_visible()
    for other in (NORMAL_HEADING, LIMITED_HEADING, FAILED_HEADING, UNAVAILABLE_HEADING):
        if other != heading:
            expect(stage.get_by_role("heading", name=other, exact=True)).to_have_count(0)
    label = stage.locator(".chairman-label")
    if case.members:
        expect(label).to_have_text("Chairman: " + CHAIRMAN.split("/", 1)[1])
    else:
        expect(label).to_have_count(0)
    if case.error_status:
        expect(stage.locator(".final-text, .verdict-summary"), "UI07: failed synthesis presented as success").to_have_count(0)
        expect(stage).to_have_class("stage stage3 stage3-failed")
        error = stage.get_by_role("alert")
        expect(error, "UI07: chairman error missing").to_be_visible()
        expect(error.locator("strong")).to_have_text("Stage 1 failed: no usable model responses" if case.peer == "not_run" else "Final synthesis could not be generated")
        expect(error.locator("p")).to_have_text(payload["stage3"]["response"])
    else:
        expect(stage.locator(".stage3-error")).to_have_count(0)
        text = stage.locator(".final-text")
        expect(text, "UI07: final synthesis missing").to_be_visible()
        expect(text, "UI07: final synthesis mismatch").to_have_text(payload["stage3"]["response"])
        assert_verdict_display(stage, case)


def assert_verdict_display(stage, case):
    summary = stage.locator(".verdict-summary")
    if case.mode == "synthesis":
        expect(summary).to_have_count(0)
        return
    expect(summary, "UI07: verdict missing").to_be_visible()
    heading = summary.locator(".verdict-heading")
    prefix = "Chairman decision (not fully peer-validated):" if case.peer in WARNINGS else "Decision:"
    expect(heading, "UI07: verdict validation label mismatch").to_contain_text(prefix)
    expect(heading.locator("strong"), "UI07: verdict value mismatch").to_have_text(VERDICT["verdict"])
    expect(heading.locator("span")).to_be_visible()
    expect(heading.locator("span"), "UI07: confidence mismatch").to_have_text("· Confidence: 83%")
    expect(summary.locator("p")).to_be_visible()
    expect(summary.locator("p"), "UI07: rationale mismatch").to_have_text(VERDICT["rationale"])
    dissent = summary.locator(".verdict-dissent")
    if case.dissent:
        expect(dissent, "UI07: dissent missing or wrong").to_be_visible()
        expect(dissent, "UI07: dissent missing or wrong").to_have_text("Dissent: " + DISSENT)
    else:
        expect(dissent).to_have_count(0)


def assert_warnings(page, case):
    if case.peer in WARNINGS:
        role = "note" if case.peer == "skipped" else "alert"
        warning = page.locator(".stage2").get_by_role(role)
        expect(warning, "UI07: peer warning missing").to_be_visible()
        expect(warning, "UI07: peer warning mismatch").to_have_text(WARNINGS[case.peer])
    else:
        expect(page.locator(".stage2 [role=alert], .stage2 [role=note]")).to_have_count(0)
    if case.peer == "skipped":
        expect(page.get_by_role("status").filter(has_text="Partial Council result:")).to_contain_text("1 of 4 configured models")


def submit_round(page, client, case):
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
    page.get_by_role("combobox", name="Mode", exact=True).select_option(case.mode)
    if case.dissent:
        page.get_by_label("Include dissent", exact=True).check()
    entry.fill(QUESTION)
    with page.expect_response(lambda r: is_response(r, "POST", detail + "/message/stream")) as streamed:
        page.get_by_role("button", name="Send", exact=True).click()
    assert streamed.value.request.post_data_json == {"content": QUESTION, "verdict_type": case.mode, "include_dissent": case.dissent}
    payload = decode_stream(streamed.value, case)
    expect(entry, "UI07: loading did not end").to_be_editable()
    expect(page.locator(".loading-indicator, .stage-loading, .council-error")).to_have_count(0)
    expect(page.get_by_text(QUESTION, exact=True)).to_be_visible()
    return cid, payload


def check_round(page, client, data, work, output, case):
    cid, payload = submit_round(page, client, case)
    assert_peer_result(case, payload, work)
    assert_chairman_result(case, payload)
    saved = json_response(client.get(f"/api/conversations/{cid}"))
    disk = json.loads((data / f"{cid}.json").read_text(encoding="utf-8"))
    assert saved == disk and disk["title"] == TITLE
    assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **payload}]
    for stage, state in expected_progress(case):
        expect(progress_locator(page, stage, state)).to_be_visible()
    expect(page.locator(".engine-progress-event")).to_have_count(len(expected_progress(case)))
    assert_final_display(page, case, payload)
    assert_warnings(page, case)
    assert_provider(case, work)
    (output / "sse.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (output / "chairman.txt").write_text(page.locator(".stage3").inner_text(), encoding="utf-8")
    return cid


def browser_case(browser, case):
    output = ARTIFACTS / case.name
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="council-ui07-") as directory:
        work = Path(directory)
        (work / SCENARIO).write_text(case.name, encoding="utf-8")
        try:
            with isolated_server(work, output, server_script="serve_ui07.py") as (client, data):
                old = data / f"{EXISTING['id']}.json"
                old.write_text(json.dumps(EXISTING), encoding="utf-8")
                original = old.read_bytes()
                with observed_browser(browser, output) as (page, network, errors, forbidden):
                    cid = check_round(page, client, data, work, output, case)
                    assert old.read_bytes() == original and len(list(data.glob("*.json"))) == 2
                    assert [r for r in network if r["method"] == "POST"] == [
                        {"method": "POST", "path": "/api/conversations", "status": 200},
                        {"method": "POST", "path": f"/api/conversations/{cid}/message/stream", "status": 200}]
                    assert not forbidden and not errors, "UI07: browser errors or external traffic"
        finally:
            for name in (JOURNAL, MAPPING):
                if (work / name).is_file():
                    shutil.copyfile(work / name, output / name)
    print(f"UI07 PASS: {case.name}", flush=True)


def main():
    assert (DIST / "index.html").is_file(), "UI07: production frontend build is missing"
    selected = os.getenv("UI07_CASE")
    cases = [CASES[selected]] if selected else list(CASES.values())
    with sync_playwright() as playwright:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            for case in cases:
                browser_case(browser, case)
        finally:
            browser.close()
    print(f"UI07 PASS: {len(cases)} Chairman scenarios", flush=True)


if __name__ == "__main__":
    main()
