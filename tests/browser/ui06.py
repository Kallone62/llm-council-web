"""UI-06: native peer review, anonymous labels and aggregate ranking in Chromium."""
import json
import os
import re

from playwright.sync_api import expect

from serve_ui04 import JOURNAL, LABELS, MODELS, REVIEW_INTRO
from serve_ui06 import A, B, C, BALLOTS, MAPPING_FILE, SCORES, review_text
from ui01 import ROOT, json_response
from ui04 import main as run_round

ARTIFACTS = ROOT / "browser-artifacts" / "ui06" / os.getenv("UI06_RUN", "normal")
# Hand-calculated from the three fixture ballots, excluding self-votes without
# renumbering positions: (model, Borda, mean position, mean raw score / 10).
AGGREGATES = [(B, .75, 1.5, .85), (A, .5, 2.0, .8), (C, .25, 2.5, .75)]


def short(model):
    return model.split("/", 1)[1]


def assert_review_result(payload):
    metadata = payload["metadata"]
    entries = metadata["label_to_model"]
    mapping = {label: entry["model"] for label, entry in entries.items()}
    assert set(mapping) == set(LABELS) and set(mapping.values()) == set(MODELS)
    assert {label: entry["display_index"] for label, entry in entries.items()} == dict(zip(LABELS, range(3)))
    for row in payload["stage2"]:
        model, parsed = row["model"], row["parsed_ranking"]
        assert row["ranking"] == review_text(model, mapping), "UI06: wrong raw review"
        assert tuple(mapping[label] for label in parsed["ranking"]) == BALLOTS[model], "UI06: wrong parsed ranking"
        assert parsed["scores"] == dict(zip(parsed["ranking"], SCORES)), "UI06: wrong parsed scores"
    assert metadata["config"]["exclude_self_votes"] is True
    assert metadata["aggregate_rankings"] == [
        {"model": model, "borda_score": borda, "average_position": position, "average_score": score,
         "vote_count": 2, "self_votes_excluded": True, "rank": rank}
        for rank, (model, borda, position, score) in enumerate(AGGREGATES, 1)
    ], "UI06: wrong native aggregate ranking"
    assert metadata["peer_review"] == {"state": "completed", "reviews": 3, "valid_reviews": 3,
        "partial_reviews": 0, "invalid_reviews": 0, "abstained_reviews": 0,
        "effective_votes": 6, "ranked_candidates": 3}
    return mapping


def assert_reviewer(stage, model, row, mapping):
    tab = stage.get_by_role("button", name=short(model), exact=True)
    expect(tab).to_be_visible()
    expect(tab).to_be_enabled()
    tab.click()
    expect(tab, "UI06: selected reviewer not active").to_have_class(re.compile(r"\bactive\b"))
    expect(stage.locator(".tabs .active")).to_have_count(1)
    identity = stage.locator(".ranking-model")
    expect(identity).to_be_visible()
    expect(identity, "UI06: reviewer identity mismatch").to_have_text(model)
    content = stage.locator(".ranking-content")
    names = [short(mapping[label]) for label in row["parsed_ranking"]["ranking"]]
    first, second, third = names
    prose = f"{REVIEW_INTRO} Reviewer {MODELS.index(model) + 1} ranks {first} above {second} and {third}."
    expect(content.locator("p"), "UI06: review prose mismatch").to_have_text(prose)
    expect(content.locator("p strong"), "UI06: de-anonymized names mismatch").to_have_text(names)
    code = content.locator("pre code")
    expect(code).to_be_visible()
    # Markdown emphasis is literal inside the JSON code fence; compare its data.
    assert json.loads(code.inner_text().replace("**", "")) == {
        "ranking": names, "scores": dict(zip(names, SCORES))}, "UI06: review score display mismatch"
    extracted = stage.locator(".parsed-ranking li")
    expect(extracted, "UI06: extracted ranking mismatch").to_have_text(names)
    return {"reviewer": identity.inner_text(), "prose": content.locator("p").inner_text(),
            "ranking": extracted.all_inner_texts(), "review_json": code.inner_text()}


def assert_aggregate(stage, rows):
    expect(stage.get_by_role("heading", name="Aggregate Rankings (Street Cred)", exact=True)).to_be_visible()
    items = stage.locator(".aggregate-item")
    expect(items).to_have_count(len(rows))
    evidence = []
    for item, row in zip(items.all(), rows):
        expect(item).to_be_visible()
        expected = {"rank-position": f"#{row['rank']}", "rank-model": short(row["model"]),
                    "rank-score": f"Borda: {row['borda_score']:.3f}", "rank-count": f"({row['vote_count']} votes)"}
        for field, value in expected.items():
            expect(item.locator(f".{field}"), f"UI06: aggregate {field} mismatch").to_have_text(value)
        evidence.append({field: item.locator(f".{field}").inner_text() for field in expected})
    return evidence


def assert_reviews(page, payload, mapping):
    stage = page.locator(".stage2")
    expect(stage.get_by_role("heading", name="Stage 2: Peer Rankings", exact=True)).to_be_visible()
    expect(stage.locator(".tabs").get_by_role("button")).to_have_count(len(MODELS))
    reviews = {row["model"]: row for row in payload["stage2"]}
    initial = payload["stage2"][0]["model"]
    others = [model for model in MODELS if model != initial]
    visits = [*others, initial, others[0], initial]
    return {"reviews": [assert_reviewer(stage, model, reviews[model], mapping) for model in visits],
            "aggregate": assert_aggregate(stage, payload["metadata"]["aggregate_rankings"])}


def check_reviews(page, client, data, work, detail, output):
    payload = json.loads((output / "sse.json").read_text(encoding="utf-8"))
    mapping = assert_review_result(payload)
    observed = json.loads((work / MAPPING_FILE).read_text(encoding="utf-8"))
    assert mapping == observed, "UI06: backend label mapping differs from provider prompt"
    before = json_response(client.get(detail))
    path = data / f"{before['id']}.json"
    disk_before, provider_before = path.read_bytes(), (work / JOURNAL).read_bytes()
    assert before == json.loads(disk_before)
    assert before["messages"][-1] == {"role": "assistant", **payload}
    evidence = assert_reviews(page, payload, mapping)
    assert json_response(client.get(detail)) == before and path.read_bytes() == disk_before
    assert (work / JOURNAL).read_bytes() == provider_before, "UI06: review inspection invoked provider"
    evidence["prompt_mapping"] = observed
    (output / "reviews.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print("UI06: 5 reviewer selections and 3 aggregate rows verified", flush=True)


def main():
    run_round(output=ARTIFACTS, after_round=check_reviews, server_script="serve_ui06.py", review_oracle=assert_review_result)
    print("UI06 PASS: native reviews, label mapping, extracted and aggregate rankings", flush=True)


if __name__ == "__main__":
    main()
