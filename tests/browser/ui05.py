"""UI-05: every Stage 1 tab selects the corresponding real Council response."""
import json
import os
import re

from playwright.sync_api import expect

from serve_ui04 import JOURNAL, MODELS, candidate
from ui01 import ROOT, json_response
from ui04 import main as run_round

ARTIFACTS = ROOT / "browser-artifacts" / "ui05" / os.getenv("UI05_RUN", "normal")


def assert_tabs(page, answers):
    # UI04 already compares the real SSE result with the provider oracle and disk.
    expected = {row["model"]: row["response"] for row in answers}
    assert len(answers) == len(MODELS) and expected == {model: candidate(model) for model in MODELS}
    stage = page.locator(".stage1")
    expect(stage.get_by_role("heading", name="Stage 1: Individual Responses", exact=True)).to_be_visible()
    tabs = stage.locator(".tabs").get_by_role("button")
    expect(tabs, "UI05: model tab count mismatch").to_have_count(len(MODELS))
    first = answers[0]["model"]
    # Start with a different model, visit all models and revisit a noninitial tab.
    others = [model for model in MODELS if model != first]
    visits = [*others, first, others[0], first]
    evidence = []
    for model in visits:
        tab = stage.get_by_role("button", name=model.split("/", 1)[1], exact=True)
        expect(tab).to_be_visible()
        expect(tab).to_be_enabled()
        tab.click()
        expect(tab, "UI05: selected tab not active").to_have_class(re.compile(r"\bactive\b"))
        expect(stage.locator(".tabs .active"), "UI05: multiple active tabs").to_have_count(1)
        identity, response = stage.locator(".model-name"), stage.locator(".response-text")
        expect(identity).to_be_visible()
        expect(response).to_be_visible()
        expect(identity, "UI05: selected model identity mismatch").to_have_text(model)
        expect(response, "UI05: selected model response mismatch").to_have_text(expected[model])
        evidence.append({"selected": model, "model": identity.inner_text(), "response": response.inner_text()})
    assert {row["selected"] for row in evidence} == set(MODELS)
    return evidence


def check_switching(page, client, data, work, detail, output):
    payload = json.loads((output / "sse.json").read_text(encoding="utf-8"))
    before = json_response(client.get(detail))
    path = data / f"{before['id']}.json"
    disk_before = path.read_bytes()
    provider_before = (work / JOURNAL).read_bytes()
    assert before == json.loads(disk_before) and before["messages"][-1]["stage1"] == payload["stage1"]
    requests = []
    page.on("request", lambda request: requests.append(request.method))
    evidence = assert_tabs(page, payload["stage1"])
    assert json_response(client.get(detail)) == before, "UI05: tab selection changed REST result"
    assert path.read_bytes() == disk_before, "UI05: tab selection changed disk result"
    assert (work / JOURNAL).read_bytes() == provider_before, "UI05: tab selection invoked provider"
    assert all(method in ("GET", "HEAD") for method in requests), "UI05: tab selection issued a write"
    (output / "tabs.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"UI05: {len(evidence)} tab selections matched model and response", flush=True)


def main():
    run_round(output=ARTIFACTS, after_round=check_switching)
    print("UI05 PASS: all model tabs and revisits match native SSE/REST/disk", flush=True)


if __name__ == "__main__":
    main()
