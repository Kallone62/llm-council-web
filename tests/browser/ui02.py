"""UI-02: save/replace a synthetic key through the real settings screen."""
import json
import os
import secrets
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from playwright.sync_api import expect, sync_playwright

from ui01 import BASE, DIST, ROOT, isolated_server, is_response

KEY_PATH = "/api/settings/openrouter-key"
ARTIFACTS = ROOT / "browser-artifacts" / "ui02" / os.getenv("UI02_RUN", "normal")


class AcceptanceError(AssertionError):
    """Only static, nonsecret messages may be reported to CI."""


def require(condition, message):
    if not condition:
        raise AcceptanceError(message)


def private(value, keys, message):
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    require(not any(key.encode() in raw for key in keys), message)


def checked_response(response, keys, expected=None):
    private(response.content, keys, "UI02: key leaked in API response")
    private(json.dumps(dict(response.headers)), keys, "UI02: key leaked in API headers")
    require(response.status_code == 200, "UI02: API request failed")
    body = response.json()
    if expected is not None:
        require(body == expected, "UI02: incorrect save response")
    return body


def verify_saved(client, work, key, keys, original_settings):
    path = work / ".env"
    require(path.is_file(), "UI02: key was not persisted")
    text = path.read_text(encoding="utf-8")
    require(dotenv_values(path).get("OPENROUTER_API_KEY") == key,
            "UI02: persisted key mismatch")
    require(text.count("OPENROUTER_API_KEY=") == 1, "UI02: duplicate persisted key")
    require(all(other == key or other not in text for other in keys),
            "UI02: old key retained on disk")
    require(dotenv_values(path).get("UI02_SENTINEL") == "keep", "UI02: unrelated env value changed")
    require("# Preserve this comment" in text, "UI02: unrelated env comment changed")
    settings = checked_response(client.get("/api/settings"), keys)
    require(settings == {**original_settings, "api_key_configured": True},
            "UI02: effective settings mismatch")
    require(checked_response(client.get("/api/conversations"), keys) == [],
            "UI02: unrelated conversation state changed")


def visible(locator, message):
    try:
        expect(locator).to_be_visible()
    except AssertionError:
        raise AcceptanceError(message) from None


def empty_input(locator):
    try:
        expect(locator).to_have_value("")
    except AssertionError:
        raise AcceptanceError("UI02: password input not cleared") from None


def open_settings(page, configured):
    with page.expect_response(lambda r: is_response(r, "GET", "/api/settings")) as response:
        page.get_by_role("button", name="⚙ Settings").click()
    require(response.value.status == 200, "UI02: settings screen API failed")
    visible(page.get_by_role("heading", name="Settings", exact=True), "UI02: settings screen missing")
    visible(page.get_by_text("Configured" if configured else "Not configured", exact=True),
            "UI02: incorrect configured indicator")


def save_key(page, key, keys, replace):
    field = page.get_by_placeholder("Enter a new key to replace it" if replace else "sk-or-v1-…")
    require(field.get_attribute("type") == "password", "UI02: key input is not masked")
    empty_input(field)
    field.fill(key)
    with page.expect_response(lambda r: is_response(r, "PUT", KEY_PATH)) as saved:
        page.get_by_role("button", name="Replace key" if replace else "Save key", exact=True).click()
    response = saved.value
    private(response.body(), keys, "UI02: key leaked in API response")
    require(response.status == 200 and response.json() == {"configured": True},
            "UI02: incorrect save response")
    require(response.request.post_data_json == {"api_key": key}, "UI02: incorrect key request")
    visible(page.get_by_text("OpenRouter key saved locally.", exact=True), "UI02: save notice missing")
    visible(page.get_by_text("Configured", exact=True), "UI02: incorrect configured indicator")
    empty_input(page.get_by_placeholder("Enter a new key to replace it"))
    try:
        expect(page.get_by_title("OpenRouter key missing")).to_have_count(0)
    except AssertionError:
        raise AcceptanceError("UI02: sidebar key status not updated") from None
    private(page.content(), keys, "UI02: key leaked in DOM")
    private(page.locator("body").inner_text(), keys, "UI02: key leaked in visible UI")


def verify_network(requests, responses, keys, expected_keys):
    puts = []
    for request in requests:
        private(request.url, keys, "UI02: key leaked in request URL")
        private(json.dumps(request.all_headers()), keys, "UI02: key leaked in request headers")
        if request.method == "PUT" and urlsplit(request.url).path == KEY_PATH:
            puts.append(request.post_data_json)
        else:
            private(request.post_data or "", keys, "UI02: key leaked in unrelated request")
    require(puts == [{"api_key": key} for key in expected_keys], "UI02: incorrect save request sequence")
    for response in responses:
        if urlsplit(response.url).path.startswith("/api/"):
            private(response.body(), keys, "UI02: key leaked in API response")
            private(json.dumps(response.headers), keys, "UI02: key leaked in API headers")


def settings_case(browser, work, client, keys, evidence):
    original_settings = checked_response(client.get("/api/settings"), keys)
    require(original_settings["api_key_configured"] is False, "UI02: key unexpectedly configured")
    config = work / "llm_council.yaml"
    original_config = config.read_bytes()
    (work / ".env").write_text("# Preserve this comment\nUI02_SENTINEL=keep\n", encoding="utf-8")
    context = browser.new_context(service_workers="block")
    requests, responses, console, errors, forbidden, network = [], [], [], [], [], []

    def guard(route):
        url = urlsplit(route.request.url)
        if url.scheme == "http" and url.netloc == "localhost:8001":
            route.continue_()
        else:
            forbidden.append(True)
            route.abort()

    context.route("**/*", guard)
    page = context.new_page()
    page.set_default_timeout(8000)
    page.on("request", requests.append)
    def observe_response(response):
        responses.append(response)
        network.append({"method": response.request.method, "path": urlsplit(response.url).path,
                        "status": response.status})

    page.on("response", observe_response)
    page.on("console", lambda message: console.append((message.type, message.text)))
    page.on("pageerror", lambda error: errors.append(str(error)))
    # A trace before entering credentials is safe; never record key input or PUT bodies.
    context.tracing.start(screenshots=True, snapshots=True, sources=False)
    try:
        try:
            page.goto(BASE + "/app/", wait_until="domcontentloaded")
            visible(page.get_by_title("OpenRouter key missing"), "UI02: initial sidebar key status missing")
            open_settings(page, False)
        finally:
            context.tracing.stop(path=str(evidence / "startup-trace.zip"))
        for index, key in enumerate(keys):
            save_key(page, key, keys, replace=bool(index))
            verify_saved(client, work, key, keys, original_settings)
            # Read response bodies before navigating away; Chromium may discard them.
            verify_network(requests, responses, keys, [key])
            requests.clear()
            responses.clear()
            # Fresh page state must come from GET, never the old password input.
            with page.expect_response(lambda r: is_response(r, "GET", "/api/settings")):
                page.reload(wait_until="domcontentloaded")
            open_settings(page, True)
            empty_input(page.get_by_placeholder("Enter a new key to replace it"))
            private(page.content(), keys, "UI02: key leaked after reload")
        verify_network(requests, responses, keys, [])
        require(config.read_bytes() == original_config, "UI02: YAML changed while saving key")
        require(not list((work / "conversations").glob("*.json")), "UI02: unexpected conversation file")
        require(not forbidden, "UI02: external browser traffic")
        require(not errors and not any(level == "error" for level, _ in console), "UI02: browser errors")
    finally:
        try:
            private(json.dumps(console + errors), keys, "UI02: key leaked in browser logs")
            # No raw console text or request/response bodies are written to artifacts.
            private(json.dumps(network), keys, "UI02: key leaked in network evidence")
            (evidence / "network.json").write_text(json.dumps(network, indent=2), encoding="utf-8")
            (evidence / "console.json").write_text(json.dumps({"levels": [level for level, _ in console],
                "page_error_count": len(errors)}), encoding="utf-8")
        finally:
            context.close()


def publish_evidence(source, keys, destination=ARTIFACTS):
    # Fail closed before copying any artifact to the CI upload directory.
    for file in source.iterdir():
        private(file.read_bytes(), keys, "UI02: key leaked in evidence")
        if file.suffix == ".zip":
            with zipfile.ZipFile(file) as archive:
                for entry in archive.namelist():
                    private(entry, keys, "UI02: key leaked in trace")
                    private(archive.read(entry), keys, "UI02: key leaked in trace")
    shutil.copytree(source, destination, dirs_exist_ok=True)


def main():
    keys = ["sk-or-v1-" + secrets.token_hex(32) for _ in range(2)]
    try:
        require((DIST / "index.html").is_file(), "UI02: production frontend build missing")
        with tempfile.TemporaryDirectory(prefix="council-ui02-") as directory:
            root = Path(directory)
            work, evidence = root / "app", root / "evidence"
            work.mkdir()
            evidence.mkdir()
            try:
                with isolated_server(work, evidence) as (client, _), sync_playwright() as playwright:
                    executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
                    browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
                    try:
                        settings_case(browser, work, client, keys, evidence)
                    finally:
                        browser.close()
            finally:
                publish_evidence(evidence, keys)
        print("UI02 PASS: save, replace, reload, privacy", flush=True)
    except AcceptanceError as error:
        # AcceptanceError messages are static; don't print Playwright exception details.
        raise SystemExit(str(error)) from None
    except BaseException as error:
        raise SystemExit(f"UI02: harness failure ({type(error).__name__})") from None


if __name__ == "__main__":
    main()
