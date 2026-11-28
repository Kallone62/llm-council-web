"""UI-07 extension: native response cache, real provider traffic and truthful warnings."""
import json
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path

import yaml
from playwright.sync_api import expect, sync_playwright

from serve_ui04 import JOURNAL, TITLE, TITLE_MODEL
from serve_ui07 import CASES, CHAIRMAN, MODELS, MAPPING, QUESTION, SCENARIO
from ui01 import BASE, DIST, EXISTING, ROOT, isolated_server, is_response, json_response, observed_browser
from ui07 import configure, expected_progress, assert_peer_result, assert_chairman_result, assert_final_display, assert_warnings
from ui13 import toggle

ARTIFACTS = ROOT / "browser-artifacts" / "ui07" / os.getenv("UI07_CACHE_RUN", "cache-normal") / "response-cache"
NOTICE = "Cached Council result: Individual models were not run again for this result."
SETTINGS = "/api/settings"


def read_journal(work):
    path = work / JOURNAL
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def assert_traffic(rows, hit):
    expected = [("title", TITLE_MODEL, 200)]
    if not hit:
        expected += [(stage, model, 200) for stage in ("stage1", "stage2") for model in MODELS]
        expected.append(("stage3", CHAIRMAN, 200))
    actual = [(r["stage"], r["model"], r["status"]) for r in rows]
    assert Counter(actual) == Counter(expected), "UI07-cache: provider traffic mismatch"
    assert [s for s, *_ in actual if s != "title"] == [s for s, *_ in expected if s != "title"]


def settings(page, client, work, enabled, ttl):
    with page.expect_response(lambda r: is_response(r, "GET", SETTINGS)) as loaded:
        page.get_by_role("button", name="⚙ Settings").click()
    previous = loaded.value.json()
    control = toggle(page, "Response cache")
    field = page.get_by_role("spinbutton", name=re.compile(r"^Cache TTL"))
    expect(control).to_have_attribute("aria-pressed", str(previous["cache_enabled"]).lower())
    expect(field).to_have_value(str(previous["cache_ttl_seconds"]))
    if previous["cache_enabled"] is not enabled:
        control.click()
    if enabled:
        expect(field).to_be_enabled()
        field.fill(str(ttl))
    else:
        expect(field).to_be_disabled()
    with page.expect_response(lambda r: is_response(r, "PUT", SETTINGS)) as saved:
        page.get_by_role("button", name="Save settings", exact=True).first.click()
    assert saved.value.status == 200
    expected = {"enabled": enabled, "ttl_seconds": ttl}
    for value in (saved.value.request.post_data_json, saved.value.json(), json_response(client.get(SETTINGS))):
        assert value["cache_enabled"] is enabled and value["cache_ttl_seconds"] == ttl, "UI07-cache: effective setting mismatch"
    config = yaml.safe_load((work / "llm_council.yaml").read_text(encoding="utf-8"))
    assert all(config["cache"][k] == v for k, v in expected.items()), "UI07-cache: persisted setting mismatch"
    expect(control).to_have_attribute("aria-pressed", str(enabled).lower())
    expect(field).to_have_value(str(ttl))


def decode(response, case, hit, enabled):
    assert response.status == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.body().decode("utf-8").splitlines() if line.startswith("data: ")]
    assert len(events) >= 5 and events[-2]["type"] == "complete"
    metadata = events[-2]["metadata"]
    if hit:
        assert metadata.get("cache_hit") is True, "UI07-cache: expected native cache hit"
    else:
        assert metadata.get("cache_hit") is not True, "UI07-cache: fresh round unexpectedly cached"
        if enabled:
            assert metadata.get("cache_hit") is False
        else:
            assert "cache_hit" not in metadata and "cache_key" not in metadata
    states = [] if hit else expected_progress(case)
    assert [e["type"] for e in events] == ["engine_progress"] * len(states) + [
        "stage1_complete", "stage2_complete", "stage3_complete", "complete", "title_complete"], "UI07-cache: SSE sequence mismatch"
    progress = [e["data"] for e in events[:-5]]
    assert [(e["stage"], e["state"]) for e in progress] == states
    assert metadata["ui_runtime_progress"] == progress
    assert events[-4]["metadata"] == events[-3]["metadata"] == metadata
    assert events[-1]["data"] == {"title": TITLE}
    return {"stage1": events[-5]["data"], "stage2": events[-4]["data"], "stage3": events[-3]["data"], "metadata": metadata}


def assert_display(page, case, payload, hit):
    notice = page.get_by_role("note").filter(has_text="Cached Council result:")
    if hit:
        expect(notice, "UI07-cache: reuse notice missing").to_be_visible()
        expect(notice).to_have_text(NOTICE)
    else:
        expect(notice, "UI07-cache: false reuse notice").to_have_count(0)
    expect(page.locator(".engine-progress-event"), "UI07-cache: invented live progress").to_have_count(0 if hit else len(expected_progress(case)))
    expect(page.locator(".loading-indicator, .stage-loading, .council-error")).to_have_count(0)
    assert_final_display(page, case, payload)
    assert_warnings(page, case)


def run_round(page, client, data, work, name, *, hit=False, enabled=True, previous=None):
    case = CASES[name]
    (work / SCENARIO).write_text(name, encoding="utf-8")
    if not hit:
        (work / MAPPING).unlink(missing_ok=True)
    rows = read_journal(work)
    before = {p.name: p.read_bytes() for p in data.glob("*.json")}
    cache_dir = work / "home" / ".cache" / "llm-council"
    cache_before = {p.name: p.read_bytes() for p in cache_dir.glob("*.json")}
    with page.expect_response(lambda r: is_response(r, "POST", "/api/conversations")) as created:
        page.get_by_role("button", name="+ New Conversation", exact=True).click()
    assert created.value.status == 200
    cid = created.value.json()["id"]
    detail = f"/api/conversations/{cid}"
    entry = page.get_by_placeholder("Ask a question or continue this conversation... (Shift+Enter for new line)")
    expect(entry).to_be_editable()
    page.get_by_role("combobox", name="Mode", exact=True).select_option(case.mode)
    page.get_by_label("Include dissent", exact=True).set_checked(case.dissent)
    entry.fill(QUESTION)
    with page.expect_response(lambda r: is_response(r, "POST", detail + "/message/stream")) as sent:
        page.get_by_role("button", name="Send", exact=True).click()
    assert sent.value.request.post_data_json == {"content": QUESTION, "verdict_type": case.mode, "include_dissent": case.dissent}
    expect(entry).to_be_editable()
    payload = decode(sent.value, case, hit, enabled)
    assert_peer_result(case, payload, work)
    assert_chairman_result(case, payload)
    assert_traffic(read_journal(work)[len(rows):], hit)
    assert read_journal(work)[:len(rows)] == rows
    record = json_response(client.get(detail))
    assert record == json.loads((data / f"{cid}.json").read_bytes())
    assert record["title"] == TITLE and record["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **payload}]
    assert {p.name: p.read_bytes() for p in data.glob("*.json") if p.name != f"{cid}.json"} == before
    if enabled:
        key = payload["metadata"]["cache_key"]
        assert re.fullmatch(r"[0-9a-f]{16}", key)
        path = cache_dir / f"{key}.json"
        assert path.is_file(), "UI07-cache: cache file missing"
        cached = json.loads(path.read_bytes())
        assert cached["_cache_key"] == key and cached["metadata"]["cache_hit"] is False
        for stage, field in (("stage1", "stage1_results"), ("stage2", "stage2_results"), ("stage3", "stage3_result")):
            assert cached[field] == payload[stage]
        if hit:
            assert previous is not None and key == previous["metadata"]["cache_key"]
            for field in ("stage1", "stage2", "stage3"):
                assert previous[field] == payload[field]
            for field in ("config", "label_to_model", "aggregate_rankings", "peer_review", "verdict", "usage"):
                assert previous["metadata"].get(field) == payload["metadata"].get(field)
    if hit or not enabled:
        assert {p.name: p.read_bytes() for p in cache_dir.glob("*.json")} == cache_before
    assert_display(page, case, payload, hit)
    return record, payload


def reload_hit(page, client, record, payload):
    with page.expect_response(lambda r: is_response(r, "GET", "/api/conversations")):
        page.reload(wait_until="domcontentloaded")
    listing = json_response(client.get("/api/conversations"))
    same_title = [r for r in listing if r["title"] == TITLE and r["message_count"] == 2]
    index = next(i for i, r in enumerate(same_title) if r["id"] == record["id"])
    with page.expect_response(lambda r: is_response(r, "GET", f"/api/conversations/{record['id']}")) as loaded:
        page.get_by_role("button", name=f"{TITLE} 2 messages", exact=True).nth(index).click()
    assert loaded.value.json() == record
    assert_display(page, CASES["synthesis"], payload, True)


def scenario(page, client, data, work):
    configure(client)
    assert json_response(client.get(SETTINGS))["cache_enabled"] is False
    with page.expect_response(lambda r: is_response(r, "GET", SETTINGS)):
        assert page.goto(BASE + "/app/", wait_until="domcontentloaded").status == 200
    settings(page, client, work, True, 0)
    _, synthesis = run_round(page, client, data, work, "synthesis")
    record, reused = run_round(page, client, data, work, "synthesis", hit=True, previous=synthesis)
    reload_hit(page, client, record, reused)
    _, binary = run_round(page, client, data, work, "binary-dissent")
    run_round(page, client, data, work, "binary-dissent", hit=True, previous=binary)
    _, partial = run_round(page, client, data, work, "partial")
    run_round(page, client, data, work, "partial", hit=True, previous=partial)
    keys = [p["metadata"]["cache_key"] for p in (synthesis, binary, partial)]
    assert len(set(keys)) == 3, "UI07-cache: request semantics collided"
    settings(page, client, work, True, 60)
    path = work / "home" / ".cache" / "llm-council" / f"{keys[0]}.json"
    aged = json.loads(path.read_bytes())
    aged["_cached_at"] = time.time() - 3600
    path.write_text(json.dumps(aged), encoding="utf-8")
    _, refreshed = run_round(page, client, data, work, "synthesis")
    assert refreshed["metadata"]["cache_key"] == keys[0]
    assert json.loads(path.read_bytes())["_cached_at"] > aged["_cached_at"]
    settings(page, client, work, False, 60)
    run_round(page, client, data, work, "synthesis", enabled=False)


def main():
    assert (DIST / "index.html").is_file(), "UI07-cache: production frontend build is missing"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright, tempfile.TemporaryDirectory(prefix="council-ui07-cache-") as directory:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        work = Path(directory)
        try:
            with isolated_server(work, ARTIFACTS, server_script="serve_ui07.py") as (client, data):
                other = data / f"{EXISTING['id']}.json"
                other.write_text(json.dumps(EXISTING), encoding="utf-8")
                original = other.read_bytes()
                with observed_browser(browser, ARTIFACTS) as (page, network, errors, forbidden):
                    scenario(page, client, data, work)
                    assert other.read_bytes() == original and len(list(data.glob("*.json"))) == 9
                    posts = [r for r in network if r["method"] == "POST"]
                    assert len(posts) == 16 and all(r["status"] == 200 for r in posts)
                    assert not errors and not forbidden, "UI07-cache: browser errors or external traffic"
                    page.screenshot(path=str(ARTIFACTS / "final.png"), full_page=True)
        finally:
            browser.close()
            if (work / JOURNAL).is_file():
                shutil.copyfile(work / JOURNAL, ARTIFACTS / JOURNAL)
            cache_dir = work / "home" / ".cache" / "llm-council"
            if cache_dir.is_dir():
                shutil.copytree(cache_dir, ARTIFACTS / "native-cache", dirs_exist_ok=True)
    print("UI07-cache PASS: 8 real rounds, cache hits, verdict isolation, partial warnings, TTL and disable", flush=True)


if __name__ == "__main__":
    main()
