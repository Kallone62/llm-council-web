"""UI-06 distinct peer ballots at the existing controlled provider HTTP boundary."""
import json
import os
import re
import sys
from functools import partial
from pathlib import Path

from serve import serve
from serve_ui04 import MODELS, LABELS, REVIEW_INTRO, classify, controlled_provider

A, B, C = MODELS
BALLOTS = {A: (B, A, C), B: (B, C, A), C: (A, B, C)}
SCORES = (9, 8, 7)
MAPPING_FILE = "review-labels.json"


def review_ballot(model, mapping):
    inverse = {author: label for label, author in mapping.items()}
    ranking = [inverse[author] for author in BALLOTS[model]]
    return {"ranking": ranking, "scores": dict(zip(ranking, SCORES))}


def review_text(model, mapping):
    ballot = review_ballot(model, mapping)
    first, second, third = ballot["ranking"]
    prose = f"{REVIEW_INTRO} Reviewer {MODELS.index(model) + 1} ranks {first} above {second} and {third}."
    return prose + "\n```json\n" + json.dumps(ballot) + "\n```"


def observe_review(model, prompt):
    mapping = {f"Response {label}": MODELS[int(index) - 1] for label, index in re.findall(
        r'<candidate_response id="([A-C])">\nCandidate ([1-3]): preserve the original archive\.\n</candidate_response>', prompt)}
    assert set(mapping) == set(LABELS) and set(mapping.values()) == set(MODELS)
    path = Path(MAPPING_FILE)
    if path.is_file():
        assert json.loads(path.read_text(encoding="utf-8")) == mapping
    else:
        path.write_text(json.dumps(mapping), encoding="utf-8")
    return review_text(model, mapping)


def expected_chairman_reviews():
    mapping = json.loads(Path(MAPPING_FILE).read_text(encoding="utf-8"))
    return [review_text(model, mapping) for model in MODELS]


async def controlled_ui06_provider(transport, request):
    # HTTPX installs this on the transport class: a regular function binds self.
    classifier = partial(classify, review_for=observe_review, chairman_reviews=expected_chairman_reviews)
    return await controlled_provider(transport, request, classifier=classifier)


if __name__ == "__main__":
    if not os.environ.get("LLM_COUNCIL_UI_DATA_DIR") or not os.environ.get("LLM_COUNCIL_CONFIG"):
        raise RuntimeError("Run the isolated browser harness, not the server directly")
    serve(sys.argv[1], async_transport=controlled_ui06_provider)
