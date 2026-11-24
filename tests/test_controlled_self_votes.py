"""Compare identical native ballots with self-vote exclusion enabled and disabled."""
import asyncio
import json
import re
from collections import Counter

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from llm_council import council
from llm_council.unified_config import get_config, reload_config

from test_controlled_profiles import (
    KEY, PROFILES, TITLE, TITLE_MODEL, decode_response, deny_external_network,
    isolated_profile_config,
)

from test_controlled_tier_scope import configure

MODELS, CHAIRMAN, _, _ = PROFILES["high"]
A, B, C, D = MODELS
QUESTION = "ENG17: Which archive verification plan should we use?"
ANSWERS = {model: f"Verification plan {i}: check the archive." for i, model in enumerate(MODELS, 1)}
BALLOTS = {A: (A, B, C, D), B: (A, C, D, B), C: (A, B, C, D), D: (B, C, D, A)}
SCORES = (10, 7, 4, 1)
# Hand-calculated from BALLOTS, using original positions and scores / 10.
# Values are (Borda, average position, normalized average score), independent of native aggregation.
EXPECTED = {
    True: {A: (0.667, 2.0, 0.7), B: (0.778, 1.67, 0.8), C: (0.556, 2.33, 0.6), D: (0.111, 3.67, 0.2)},
    False: {A: (0.75, 1.75, 0.775), B: (0.583, 2.25, 0.625), C: (0.5, 2.5, 0.55), D: (0.167, 3.5, 0.25)},
}
ORDER = {True: (B, A, C, D), False: (A, B, C, D)}
FINAL = {True: "Plan 2 wins without self-votes.", False: "Plan 1 wins with self-votes."}


def install_ballot_provider(monkeypatch, exclude):
    calls, label_map, reviews, prompts = [], {}, {}, []
    deny_external_network(monkeypatch, "Unexpected external network in self-vote acceptance")

    async def handle(_transport, request):
        assert (request.method, str(request.url), request.headers.get("authorization")) == (
            "POST", "https://openrouter.ai/api/v1/chat/completions", f"Bearer {KEY}")
        body = json.loads(request.content)
        model, prompt = body["model"], body["messages"][-1]["content"]
        assert QUESTION in prompt
        if prompt.startswith("Generate a very short title"):
            stage, answer = "title", TITLE
            assert model == TITLE_MODEL
        elif prompt == QUESTION:
            stage, answer = "stage1", ANSWERS[model]
        elif "<responses_to_evaluate>" in prompt:
            stage = "stage2"
            observed = {f"Response {label}": MODELS[int(index) - 1] for label, index in re.findall(
                r'<candidate_response id="([A-D])">\nVerification plan ([1-4]): check the archive\.\n</candidate_response>', prompt)}
            assert len(observed) == 4 and set(observed.values()) == set(MODELS)
            if label_map:
                assert observed == label_map
            label_map.update(observed)
            labels = {author: label for label, author in observed.items()}
            ranking = [labels[author] for author in BALLOTS[model]]
            ballot = {"ranking": ranking, "scores": dict(zip(ranking, SCORES))}
            answer = "All four plans ranked.\n```json\n" + json.dumps(ballot) + "\n```"
            reviews[model] = (ballot, answer)
        else:
            stage, answer = "stage3", FINAL[exclude]
            assert model == CHAIRMAN and prompt.startswith("You are the Chairman of an LLM Council.")
            assert all(value in prompt for value in ANSWERS.values())
            assert all(raw in prompt for _, raw in reviews.values())
            # Check ranked rows independently of the heading.
            assert "AGGREGATE RANKINGS" in prompt
            rows = "\n".join(f"  #{i}. {m} (avg score: {EXPECTED[exclude][m][2]}, votes: {3 if exclude else 4})"
                             for i, m in enumerate(ORDER[exclude], 1))
            assert rows in prompt
            prompts.append(prompt)
        calls.append((stage, model))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0125}})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return calls, label_map, reviews, prompts


def expected_rankings(exclude):
    return [
        {"model": model, "borda_score": EXPECTED[exclude][model][0],
         "average_position": EXPECTED[exclude][model][1], "average_score": EXPECTED[exclude][model][2],
         "vote_count": 3 if exclude else 4, "self_votes_excluded": exclude, "rank": rank}
        for rank, model in enumerate(ORDER[exclude], 1)]


def assert_heading(prompt, heading):
    assert [line for line in prompt.splitlines() if line.startswith("AGGREGATE RANKINGS")] == [heading]


HEADINGS = {True: "AGGREGATE RANKINGS (after excluding self-votes):",
            False: "AGGREGATE RANKINGS (including self-votes):"}


def assert_aggregation(payload, exclude, mapping, reviews):
    metadata = payload["metadata"]
    assert metadata["config"]["exclude_self_votes"] is exclude
    assert metadata.get("status") not in ("partial", "failed")
    assert {label: entry["model"] for label, entry in metadata["label_to_model"].items()} == mapping
    assert len(payload["stage2"]) == 4 and {r["model"] for r in payload["stage2"]} == set(MODELS)
    for result in payload["stage2"]:
        ballot, raw = reviews[result["model"]]
        assert result["ranking"] == raw
        parsed = result["parsed_ranking"]
        assert parsed["ranking"] == ballot["ranking"] and parsed["scores"] == ballot["scores"]
        assert tuple(mapping[label] for label in parsed["ranking"]) == BALLOTS[result["model"]]
    assert metadata["aggregate_rankings"] == expected_rankings(exclude)
    assert metadata["peer_review"] == {"state": "completed", "reviews": 4, "valid_reviews": 4,
        "partial_reviews": 0, "invalid_reviews": 0, "abstained_reviews": 0,
        "effective_votes": 12 if exclude else 16, "ranked_candidates": 4}
    assert payload["stage3"] == {"model": CHAIRMAN, "response": FINAL[exclude]}
    assert metadata["usage"]["total"]["total_tokens"] == 162


@pytest.mark.parametrize("modes", [(True, False), (False, True)], ids=["exclude-to-include", "include-to-exclude"])
@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
def test_native_self_vote_switch(isolated_profile_config, monkeypatch, modes, stream):
    from backend import main, storage

    assert main.run_full_council is council.run_full_council
    completed = {}
    with TestClient(main.app) as client:
        for exclude in modes:
            calls, mapping, reviews, prompts = install_ballot_provider(monkeypatch, exclude)
            response = client.put("/api/settings", json={"profile": "high", "exclude_self_votes": exclude})
            assert response.status_code == 200 and response.json()["exclude_self_votes"] is exclude
            saved = yaml.safe_load(isolated_profile_config.read_text(encoding="utf-8"))
            assert saved["council"]["exclude_self_votes"] is exclude
            reload_config()
            assert get_config().council.exclude_self_votes is exclude
            assert client.get("/api/settings").json()["exclude_self_votes"] is exclude and not calls
            created = client.post("/api/conversations", json={})
            assert created.status_code == 200
            cid = created.json()["id"]
            endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
            payload = decode_response(client.post(endpoint, json={"content": QUESTION}), stream)
            assert len(prompts) == 1
            assert_heading(prompts[0], HEADINGS[exclude])
            assert Counter(calls) == Counter([("title", TITLE_MODEL), ("stage3", CHAIRMAN)] + [
                (stage, model) for stage in ("stage1", "stage2") for model in MODELS])
            assert len(payload["stage1"]) == 4 and {r["model"]: r["response"] for r in payload["stage1"]} == ANSWERS
            assert_aggregation(payload, exclude, mapping, reviews)
            disk = storage.get_conversation(cid)
            assert disk["title"] == TITLE
            assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **payload}]
            completed[cid] = disk
            for record_id, record in completed.items():
                assert storage.get_conversation(record_id) == record
                assert client.get(f"/api/conversations/{record_id}").json() == record


@pytest.mark.parametrize("exclude", [True, False])
def test_native_chairman_heading(tmp_path, monkeypatch, exclude):
    configure(tmp_path, monkeypatch, "high", False)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    path = tmp_path / "tier.yaml"
    settings = yaml.safe_load(path.read_text(encoding="utf-8"))
    settings["council"]["exclude_self_votes"] = exclude
    path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    reload_config()
    calls, _, _, prompts = install_ballot_provider(monkeypatch, exclude)
    stage1, stage2, stage3, metadata = asyncio.run(council.run_full_council(QUESTION, bypass_cache=True))
    assert len(stage1) == len(stage2) == 4
    assert metadata["config"]["exclude_self_votes"] is exclude
    assert metadata["aggregate_rankings"] == expected_rankings(exclude)
    assert stage3 == {"model": CHAIRMAN, "response": FINAL[exclude]}
    assert Counter(calls) == Counter([("stage3", CHAIRMAN)] + [
        (stage, model) for stage in ("stage1", "stage2") for model in MODELS])
    assert len(prompts) == 1
    assert_heading(prompts[0], HEADINGS[exclude])


@pytest.mark.parametrize("flags", [(None, True), (False, True), (1, True)],
                         ids=["missing", "mixed", "non-boolean"])
def test_native_chairman_heading_unknown(tmp_path, monkeypatch, flags):
    configure(tmp_path, monkeypatch, "high", False)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    reload_config()
    rows = expected_rankings(True)
    if flags[0] is None:
        rows[0].pop("self_votes_excluded")
    else:
        rows[0]["self_votes_excluded"] = flags[0]
    rows[1]["self_votes_excluded"] = flags[1]
    calls, _, _, prompts = install_ballot_provider(monkeypatch, True)
    stage1 = [{"model": model, "response": ANSWERS[model]} for model in MODELS]
    result, _, _ = asyncio.run(council.stage3_synthesize_final(QUESTION, stage1, [], rows))
    assert result == {"model": CHAIRMAN, "response": FINAL[True]}
    assert calls == [("stage3", CHAIRMAN)] and len(prompts) == 1
    assert_heading(prompts[0], "AGGREGATE RANKINGS:")
