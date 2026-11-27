"""UI-13: independent Free -> native profile transitions and feature boundaries."""
import json
import os
import re
import tempfile
from pathlib import Path

import yaml
from playwright.sync_api import expect, sync_playwright

from ui01 import BASE, DIST, ROOT, EXISTING, isolated_server, is_response, json_response, observed_browser
from ui03 import (PROFILES, SETTINGS, NOTICE, assert_disk, assert_effective,
                  check_chat, check_notice, check_settings, open_settings, profile_select)

ARTIFACTS = ROOT / "browser-artifacts" / "ui13" / os.getenv("UI13_RUN", "normal")
PAID = ("quick", "balanced", "high", "reasoning")
FEATURES = {"Rubric scoring": "rubric_enabled", "Bias audit": "bias_audit_enabled",
            "Safety gate": "safety_enabled"}


def toggle(page, name):
    return page.locator(".toggle-row").filter(has=page.get_by_text(name, exact=True)).locator("button.toggle")


def check_controls(page, profile, enabled=False):
    free = profile == "free"
    defaults = toggle(page, "Use profile defaults")
    chairman = page.locator('input[list="known-models"]')
    style = page.get_by_role("combobox", name=re.compile(r"^Style normalization\b"))
    expect(defaults).to_have_attribute("aria-pressed", "true")
    expect(chairman).to_have_value(PROFILES[profile][1] if free else "")
    expect(chairman).to_have_attribute("placeholder", f"Auto — {PROFILES[profile][1]}")
    expect(style).to_have_value("on" if enabled else "off")
    for name in FEATURES:
        expect(toggle(page, name), "UI13: feature draft value mismatch").to_have_attribute(
            "aria-pressed", "true" if enabled else "false")
    for control in (defaults, chairman, style, *(toggle(page, name) for name in FEATURES)):
        if free:
            expect(control, "UI13: Free restriction missing").to_be_disabled()
        else:
            expect(control, "UI13: native profile control locked").to_be_enabled()
    model = page.locator(".model-option input").first
    custom = page.get_by_placeholder("provider/model-id")
    add = page.get_by_role("button", name="Add model", exact=True)
    expect(model).to_be_disabled()
    expect(custom).to_be_disabled()
    expect(add).to_be_disabled()
    if not free:
        defaults.click()
        expect(defaults).to_have_attribute("aria-pressed", "false")
        expect(model, "UI13: native custom model control locked").to_be_enabled()
        expect(custom).to_be_enabled()
        expect(add).to_be_enabled()
        defaults.click()
        check_settings(page, profile)


def check_features(payload, enabled):
    assert payload["style_normalization"] == ("on" if enabled else "off"), "UI13: effective normalization mismatch"
    for key in FEATURES.values():
        assert payload[key] is enabled, f"UI13: effective feature mismatch: {key}"


def check_persisted(path, profile, native_tier, enabled):
    assert_disk(path, profile, native_tier)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["council"]["style_normalization"] is enabled, "UI13: persisted normalization mismatch"
    evaluation = doc["evaluation"]
    for section, key in (("rubric", "enabled"), ("bias", "audit_enabled"), ("safety", "enabled")):
        assert evaluation[section][key] is enabled, f"UI13: persisted feature mismatch: {section}"


def save(page, client, path, profile, tier, enabled):
    with page.expect_response(lambda r: is_response(r, "PUT", SETTINGS)) as saved:
        page.get_by_role("button", name="Save settings", exact=True).first.click()
    response = saved.value
    assert response.status == 200, "UI13: profile save rejected"
    request = response.request.post_data_json
    assert request["profile"] == profile and request["use_profile_models"] is True
    assert request["chairman"] is None or (profile == "free" and request["chairman"] == PROFILES["free"][1])
    check_features(request, enabled)
    check_persisted(path, profile, tier, enabled)
    for payload in (response.json(), json_response(client.get(SETTINGS))):
        assert_effective(payload, profile)
        check_features(payload, enabled)
    expect(page.get_by_text("Settings saved. New council requests will use them immediately.", exact=True)).to_be_visible()
    check_settings(page, profile)
    check_controls(page, profile, enabled)


def reload_profile(page, client, path, profile, tier, enabled):
    # Check the live App callback before a reload could hide a stale profile.
    check_chat(page, profile)
    with page.expect_response(lambda r: is_response(r, "GET", SETTINGS)) as loaded:
        assert page.reload(wait_until="domcontentloaded").status == 200
    assert loaded.value.status == 200
    assert_effective(loaded.value.json(), profile)
    check_features(loaded.value.json(), enabled)
    check_notice(page, NOTICE, profile == "free")
    open_settings(page, profile)
    check_controls(page, profile, enabled)
    check_persisted(path, profile, tier, enabled)
    check_features(json_response(client.get(SETTINGS)), enabled)


def scenario(page, client, path, profile):
    initial = path.read_bytes()
    assert_effective(json_response(client.get(SETTINGS)), "free")
    check_persisted(path, "free", "high", False)
    with page.expect_response(lambda r: is_response(r, "GET", SETTINGS)) as loaded:
        assert page.goto(BASE + "/app/", wait_until="domcontentloaded").status == 200
    assert loaded.value.status == 200
    assert_effective(loaded.value.json(), "free")
    check_notice(page, NOTICE, True)
    check_chat(page, "free")
    open_settings(page, "free")
    check_controls(page, "free")

    profile_select(page).select_option(profile)
    check_settings(page, profile)
    check_controls(page, profile)
    page.get_by_role("combobox", name=re.compile(r"^Style normalization\b")).select_option("on")
    for name in FEATURES:
        toggle(page, name).click()
    check_controls(page, profile, True)
    assert path.read_bytes() == initial, "UI13: unsaved native draft changed YAML"
    assert_effective(json_response(client.get(SETTINGS)), "free")
    save(page, client, path, profile, profile, True)
    reload_profile(page, client, path, profile, profile, True)

    paid_bytes = path.read_bytes()
    profile_select(page).select_option("free")
    check_settings(page, "free")
    check_controls(page, "free")
    assert path.read_bytes() == paid_bytes, "UI13: unsaved Free draft changed YAML"
    assert_effective(json_response(client.get(SETTINGS)), profile)
    check_features(json_response(client.get(SETTINGS)), True)
    save(page, client, path, "free", profile, False)
    reload_profile(page, client, path, "free", profile, False)
    check_chat(page, "free")


def browser_case(browser, profile):
    output = ARTIFACTS / profile
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="council-ui13-") as directory:
        work = Path(directory)
        with isolated_server(work, output) as (client, data):
            record = data / f"{EXISTING['id']}.json"
            record.write_text(json.dumps(EXISTING), encoding="utf-8")
            before = record.read_bytes()
            with observed_browser(browser, output) as (page, network, errors, forbidden):
                scenario(page, client, work / "llm_council.yaml", profile)
                assert [r for r in network if r["method"] not in ("GET", "HEAD")] == [
                    {"method": "PUT", "path": SETTINGS, "status": 200}] * 2
                assert record.read_bytes() == before and list(data.iterdir()) == [record]
                assert json_response(client.get(f"/api/conversations/{EXISTING['id']}")) == EXISTING
                assert not forbidden and not errors, f"UI13: unexpected browser traffic/errors: {forbidden}, {errors}"
                page.screenshot(path=str(output / "free-restored.png"), full_page=True)
    print(f"UI13 PASS: Free -> {profile} -> Free, controls and persisted effective features", flush=True)


def main():
    assert (DIST / "index.html").is_file(), "UI13: production frontend build is missing"
    with sync_playwright() as playwright:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            for profile in PAID:
                browser_case(browser, profile)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
