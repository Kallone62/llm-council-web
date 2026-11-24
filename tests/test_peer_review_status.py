"""Stage 2 result boundary: do not confuse received critiques with usable ballots."""
import asyncio
import copy

import pytest

from backend import engine_runtime


LABELS = {f"Response {letter}": {"model": f"provider/{letter.lower()}", "display_index": i}
          for i, letter in enumerate("ABC")}
STAGE1 = [{"model": item["model"], "response": "answer"} for item in LABELS.values()]


def review(ranking, **parsed):
    return {"model": "provider/a", "ranking": "Raw review",
            "parsed_ranking": {"ranking": ranking, **parsed}}


@pytest.mark.parametrize("reviews,state,counts", [
    ([], "failed", (0, 0, 0, 0)),
    ([review([])], "failed", (0, 0, 1, 0)),
    ([review(["Response Z"])], "failed", (0, 0, 1, 0)),
    ([review(["Response A"], abstained=True)], "failed", (0, 0, 0, 1)),
    ([{"ranking": "unparseable", "parsed_ranking": None}], "failed", (0, 0, 1, 0)),
    ([review(["Response A", "Response A", {}, "Response Z"])], "partial", (0, 1, 0, 0)),
    ([review(["Response B", "Response C"])], "partial", (0, 1, 0, 0)),
    ([review(list(LABELS)), review([])], "partial", (1, 0, 1, 0)),
    ([review(list(LABELS)), review([], abstained=True)], "partial", (1, 0, 0, 1)),
    ([review(list(LABELS))], "completed", (1, 0, 0, 0)),
])
def test_stage2_reports_actual_ballot_status(reviews, state, counts):
    events = []
    usage = {"total_tokens": 17}
    native_result = (reviews, LABELS, usage)
    original = copy.deepcopy(native_result)

    async def stage(*args, **kwargs):
        return native_result

    async def capture(event):
        events.append(event)

    async def run():
        token = engine_runtime.watch_progress(capture)
        try:
            return await engine_runtime.wrap_stage("stage2", stage)("question", STAGE1)
        finally:
            engine_runtime.unwatch_progress(token)

    assert asyncio.run(run()) is native_result
    assert native_result == original
    assert events == [{"stage": "stage2", "state": "started"}, {
        "stage": "stage2", "state": state, "reviews": len(reviews),
        "valid_reviews": counts[0], "partial_reviews": counts[1],
        "invalid_reviews": counts[2], "abstained_reviews": counts[3],
    }]


@pytest.mark.parametrize("reviews,votes,state", [
    ([], [0, 0, 0], "failed"),
    ([review([])], [1, 1, 1], "failed"),  # stale/inconsistent aggregate cannot prove review
    ([review(list(LABELS))], [0, 0, 0], "failed"),
    ([review(list(LABELS))], [1, 0, 0], "partial"),
    ([review(["Response A", "Response B"])], [1, 1, 0], "partial"),
    ([review(list(LABELS))], [1, 1, 1], "completed"),
])
def test_peer_review_status_uses_native_effective_votes(reviews, votes, state):
    metadata = {"label_to_model": LABELS, "aggregate_rankings": [
        {"model": item["model"], "vote_count": count} for item, count in zip(STAGE1, votes)
    ], "verdict": {"verdict": "approved"}, "quality_metrics": {"native": True}}
    original = copy.deepcopy(metadata)
    result = engine_runtime.annotate_peer_review(STAGE1, reviews, {"response": "synthesis"}, metadata)
    assert result["peer_review"]["state"] == state
    assert result["peer_review"]["effective_votes"] == sum(votes)
    assert result["peer_review"]["ranked_candidates"] == sum(v > 0 for v in votes)
    assert result["aggregate_rankings"] == original["aggregate_rankings"]
    assert result["verdict"] == original["verdict"]
    assert metadata == original
    if state == "completed":
        assert "status" not in result
        assert result["quality_metrics"] == original["quality_metrics"]
    else:
        assert result["status"] == "partial"
        if state == "failed":
            assert result["quality_metrics"] is None


def test_native_single_model_skip_is_not_review_failure():
    metadata = {"degraded_mode": "single_model", "aggregate_rankings": [{"model": "provider/a", "vote_count": 0}]}
    result = engine_runtime.annotate_peer_review(STAGE1[:1], [], {"response": "synthesis"}, metadata)
    assert result["peer_review"]["state"] == "skipped"
    assert "status" not in result
    assert result["degraded_mode"] == "single_model"


def test_stage1_failure_is_not_a_stage2_failure():
    metadata = {"status": "failed"}
    result = engine_runtime.annotate_peer_review([], [], {"error_status": "no_responses"}, metadata)
    assert result["peer_review"]["state"] == "not_run"
    assert result["status"] == "failed"


def test_peer_review_failure_does_not_overwrite_chairman_failure():
    result = engine_runtime.annotate_peer_review(STAGE1, [], {"error_status": "empty_synthesis"}, {"status": "failed"})
    assert result["peer_review"]["state"] == "failed"
    assert result["status"] == "failed"


def test_quality_repair_cannot_restore_invalid_peer_metrics(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("Failed peer review cannot be scored")
    monkeypatch.setattr(engine_runtime, "calculate_quality_metrics", forbidden)
    metadata = {"peer_review": {"state": "failed"}, "quality_metrics": None,
                "aggregate_rankings": [{"model": "provider/a", "average_position": 1.0},
                                       {"model": "provider/b", "average_position": 2.0}]}
    result = engine_runtime.repair_quality_metrics(STAGE1, [], {"response": "synthesis"}, metadata)
    assert result["quality_metrics"] is None


@pytest.mark.parametrize("reviews,state,vote_total", [
    ([], "failed", 0),
    ([review([], abstained=True)], "failed", 0),
    ([review(["Response A"])], "failed", 0),
    ([review(list(LABELS))], "partial", 2),
])
def test_native_aggregate_does_not_make_zero_vote_candidates_reviewed(reviews, state, vote_total, monkeypatch):
    monkeypatch.setattr(engine_runtime.council, "_get_exclude_self_votes", lambda: True)
    aggregate = engine_runtime.council.calculate_aggregate_rankings(reviews, LABELS)
    result = engine_runtime.annotate_peer_review(STAGE1, reviews, {"response": "synthesis"}, {
        "label_to_model": LABELS, "aggregate_rankings": aggregate,
    })
    assert result["peer_review"]["state"] == state
    assert result["peer_review"]["effective_votes"] == vote_total
    assert result["aggregate_rankings"] is aggregate
