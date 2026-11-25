"""UI-03: profile selection, native-effective settings, YAML and Free notices."""
import json
import os
import re
import tempfile
from pathlib import Path

import yaml
from playwright.sync_api import expect, sync_playwright

from ui01 import BASE, DIST, ROOT, EXISTING, isolated_server, is_response, json_response, observed_browser

ARTIFACTS = ROOT / "browser-artifacts" / "ui03" / os.getenv("UI03_RUN", "normal")
# Independent expectations from the pinned native pools and shipped Free preset.
PROFILES = {
    "free": (["google/gemma-4-31b-it:free", "qwen/qwen3.8-27b:free",
              "nvidia/nemotron-3-super-120b-a12b:free"], "google/gemma-4-31b-it:free"),
    "quick": (["google/gemini-3.5-flash-lite", "deepseek/deepseek-v4-flash",
               "anthropic/claude-haiku-4.5"], "anthropic/claude-haiku-4.5"),
    "balanced": (["openai/gpt-5.6-luna", "anthropic/claude-sonnet-5",
                  "deepseek/deepseek-v4-flash", "google/gemini-3.7-flash"], "anthropic/claude-sonnet-5"),
    "high": (["openai/gpt-5.6-sol", "anthropic/claude-opus-5",
              "deepseek/deepseek-v4-pro-0813", "z-ai/glm-5.3"], "openai/gpt-5.6-sol"),
    "reasoning": (["openai/gpt-5.6-sol-pro", "anthropic/claude-opus-5",
                   "z-ai/glm-5.3", "deepseek/deepseek-v4-pro-0813"], "anthropic/claude-opus-5"),
}
TRANSITIONS = ("quick", "balanced", "high", "reasoning", "free", "quick")
SETTINGS = "/api/settings"
NOTICE = "You're using Free models."
SETTINGS_NOTICE = "Free is for trying the Council."


def assert_effective(payload, profile):
    models, chairman = PROFILES[profile]
    assert payload["profile"] == profile, "UI03: effective profile mismatch"
    assert payload["models"] == models, "UI03: effective model pool mismatch"
    assert payload["use_profile_models"] is True, "UI03: profile defaults not active"
    assert payload["resolved_chairman"] == chairman, "UI03: effective Chairman mismatch"
    if profile == "free":
        assert payload["chairman"] == chairman
        assert payload["style_normalization"] == "off"
        assert all(payload[key] is False for key in ("rubric_enabled", "safety_enabled", "bias_audit_enabled"))
    else:
        assert not payload["chairman"], "UI03: stale explicit Chairman"
    assert payload["api_key_configured"] is False
    assert not payload["environment_overrides"]


def assert_disk(path, profile, native_tier):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    models, chairman = PROFILES[profile]
    assert document["tiers"]["default"] == native_tier, "UI03: persisted native tier mismatch"
    assert document["council"]["models"] == models, "UI03: persisted model pool mismatch"
    if profile == "free":
        assert document["council"]["chairman"] == chairman
        assert document["council"]["style_normalization"] is False
        assert document["gateways"]["default"] == "openrouter"
        assert document["gateways"]["fallback"]["enabled"] is False
        assert document["evaluation"]["rubric"]["enabled"] is False
        assert document["evaluation"]["safety"]["enabled"] is False
        assert document["evaluation"]["bias"]["audit_enabled"] is False
    else:
        assert "chairman" not in document["council"], "UI03: persisted stale Chairman"
        assert document["gateways"]["fallback"]["enabled"] is True


def profile_select(page):
    return page.get_by_role("combobox", name=re.compile(r"^Profile\b"))


def check_notice(page, text, free):
    note = page.get_by_role("note").filter(has_text=text)
    if free:
        expect(note, "UI03: Free notice missing").to_be_visible()
    else:
        expect(note, "UI03: Free notice not hidden").to_have_count(0)


def check_settings(page, profile):
    expect(profile_select(page), "UI03: visible profile mismatch").to_have_value(profile)
    check_notice(page, SETTINGS_NOTICE, profile == "free")
    expect(page.locator(".profile-summary .model-chip"), "UI03: visible model pool mismatch").to_have_text(PROFILES[profile][0])


def open_settings(page, profile):
    with page.expect_response(lambda r: is_response(r, "GET", SETTINGS)) as loaded:
        page.get_by_role("button", name="⚙ Settings").click()
    assert loaded.value.status == 200
    assert_effective(loaded.value.json(), profile)
    check_settings(page, profile)


def check_chat(page, profile):
    page.get_by_role("button", name="Existing archive conversation 1 messages").click()
    expect(page.get_by_text("Keep this original record.", exact=True),
           "UI03: existing conversation missing after selection").to_be_visible()
    check_notice(page, NOTICE, profile == "free")


def check_profiles(page, client, data, path):
    record = data / f"{EXISTING['id']}.json"
    before = record.read_bytes()
    initial = json_response(client.get(SETTINGS))
    assert_effective(initial, "free")
    assert_disk(path, "free", "high")
    with page.expect_response(lambda r: is_response(r, "GET", SETTINGS)) as loaded:
        response = page.goto(BASE + "/app/", wait_until="domcontentloaded")
    assert response and response.status == 200 and loaded.value.status == 200
    assert_effective(loaded.value.json(), "free")
    check_notice(page, NOTICE, True)
    check_chat(page, "free")
    current, native_tier = "free", "high"
    for profile in TRANSITIONS:
        open_settings(page, current)
        disk_before = path.read_bytes()
        profile_select(page).select_option(profile)
        check_settings(page, profile)
        assert path.read_bytes() == disk_before, "UI03: unsaved profile changed YAML"
        assert_effective(json_response(client.get(SETTINGS)), current)
        with page.expect_response(lambda r: is_response(r, "PUT", SETTINGS)) as saved:
            page.get_by_role("button", name="Save settings", exact=True).first.click()
        response = saved.value
        assert response.status == 200, "UI03: profile save API failed"
        assert response.request.post_data_json["profile"] == profile
        assert response.request.post_data_json["use_profile_models"] is True
        native_tier = profile if profile != "free" else native_tier
        # Independent disk, effective response and fresh GET, not just the draft state.
        assert_disk(path, profile, native_tier)
        assert_effective(response.json(), profile)
        assert_effective(json_response(client.get(SETTINGS)), profile)
        expect(page.get_by_text("Settings saved. New council requests will use them immediately.", exact=True)).to_be_visible()
        check_settings(page, profile)
        # This detects a stale App profile callback before a reload can conceal it.
        check_chat(page, profile)
        with page.expect_response(lambda r: is_response(r, "GET", SETTINGS)) as reloaded:
            response = page.reload(wait_until="domcontentloaded")
        assert response and response.status == 200 and reloaded.value.status == 200
        assert_effective(reloaded.value.json(), profile)
        expect(page.get_by_role("heading", name="Welcome to LLM Council")).to_be_visible()
        check_notice(page, NOTICE, profile == "free")
        open_settings(page, profile)
        assert_disk(path, profile, native_tier)
        check_chat(page, profile)
        current = profile
        print(f"UI03 profile saved and reloaded: {profile}", flush=True)
    assert record.read_bytes() == before and list(data.glob("*.json")) == [record]
    assert json_response(client.get(f"/api/conversations/{EXISTING['id']}")) == EXISTING


def browser_case(browser):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="council-ui03-") as directory:
        work = Path(directory)
        with isolated_server(work, ARTIFACTS) as (client, data):
            (data / f"{EXISTING['id']}.json").write_text(json.dumps(EXISTING), encoding="utf-8")
            with observed_browser(browser, ARTIFACTS) as (page, network, errors, forbidden):
                check_profiles(page, client, data, work / "llm_council.yaml")
                writes = [r for r in network if r["method"] not in ("GET", "HEAD")]
                assert writes == [{"method": "PUT", "path": SETTINGS, "status": 200}] * len(TRANSITIONS)
                assert not forbidden, "UI03: unexpected external browser traffic"
                assert not errors, f"UI03: browser errors: {errors}"
    print("UI03 PASS: all six profile transitions", flush=True)


def main():
    assert (DIST / "index.html").is_file(), "UI03: production frontend build is missing"
    with sync_playwright() as playwright:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            browser_case(browser)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
