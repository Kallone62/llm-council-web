"""UI-07 controlled HTTP replies; all decisions and error states are native."""
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from serve import serve
from serve_ui04 import JOURNAL, KEY, TITLE, TITLE_MODEL

# Pinned high pool: four reviewers are needed for a real 1.5 SD dissent.
MODELS = ["openai/gpt-5.6-sol", "anthropic/claude-opus-5", "deepseek/deepseek-v4-pro-0813", "z-ai/glm-5.3"]
CHAIRMAN = MODELS[0]
QUESTION = "UI07: Should we migrate the archive before verification?"
FINAL = "Retain the archive until integrity checks complete."
VERDICT = {"verdict": "rejected", "confidence": 0.83, "rationale": "Integrity has not been established; retain the archive."}
DISSENT = f"Minority perspective: {MODELS[-1]} scored significantly lower (gave 1.0, median was 9.0)"
LABELS = [f"Response {letter}" for letter in "ABCD"]
SCENARIO = "ui07-scenario.txt"
MAPPING = "ui07-labels.json"


@dataclass(frozen=True)
class Case:
    name: str
    mode: str = "synthesis"
    peer: str = "completed"
    dissent: bool = False
    tied: bool = False
    failure: str = ""
    empty_reviews: bool = False

    @property
    def effective_mode(self):
        return "tie_breaker" if self.tied else self.mode

    @property
    def members(self):
        return [] if self.peer == "not_run" else [CHAIRMAN] if self.peer == "skipped" else MODELS

    @property
    def error_status(self):
        if self.peer == "not_run":
            return "no_responses"
        if self.failure == "auth":
            return "auth_error"
        return "empty_synthesis" if self.failure else None

    @property
    def final_content(self):
        if self.failure in ("empty", "whitespace", "null"):
            return {"empty": "", "whitespace": " \n\t", "null": None}[self.failure]
        if self.mode == "synthesis":
            return FINAL
        value = dict(VERDICT)
        if self.effective_mode == "tie_breaker":
            value["deadlock_resolution"] = "The integrity concern outweighs convenience."
        return json.dumps(value)


CASES = {case.name: case for case in (
    Case("synthesis"), Case("binary-dissent", "binary", dissent=True),
    Case("explicit-tie", "tie_breaker"), Case("auto-tie", "binary", tied=True),
    Case("partial", "binary", "partial"), Case("failed-review", "binary", "failed"),
    Case("empty-review", peer="failed", empty_reviews=True),
    Case("skipped-review", "binary", "skipped"),
    Case("empty", failure="empty"), Case("whitespace", "binary", failure="whitespace"),
    Case("null", "tie_breaker", failure="null"), Case("chairman-auth", "binary", failure="auth"),
    Case("no-responses", peer="not_run"),
)}


def candidate(model):
    return f"Archive candidate {MODELS.index(model) + 1}: retain the backup."


def ballot(case, model, mapping):
    if case.peer == "failed" or (case.peer == "partial" and model == MODELS[-1]):
        return {"ranking": ["Response Z"], "scores": {"Response Z": 9}}
    own = next(label for label, author in mapping.items() if author == model)
    offset = LABELS.index(own) if case.tied else 0
    ranking = LABELS[offset:] + LABELS[:offset]
    scores = dict.fromkeys(LABELS, 9)
    if case.dissent and model == MODELS[-1]:
        scores[ranking[-1]] = 1
    return {"ranking": ranking, "scores": scores}


def review(case, model, mapping):
    return f"{model}: Archive retention is prudent.\n```json\n" + json.dumps(ballot(case, model, mapping)) + "\n```"


def observe_mapping(prompt):
    mapping = {f"Response {label}": MODELS[int(index) - 1] for label, index in re.findall(
        r'<candidate_response id="([A-D])">\nArchive candidate ([1-4]): retain the backup\.\n</candidate_response>', prompt)}
    assert set(mapping) == set(LABELS) and set(mapping.values()) == set(MODELS)
    path = Path(MAPPING)
    if path.is_file():
        assert json.loads(path.read_text(encoding="utf-8")) == mapping
    else:
        path.write_text(json.dumps(mapping), encoding="utf-8")
    return mapping


def chairman_reply(case, model, prompt):
    assert model == CHAIRMAN and case.members and "AGGREGATE RANKINGS" in prompt
    if case.peer != "skipped" and not case.empty_reviews:
        mapping = json.loads(Path(MAPPING).read_text(encoding="utf-8"))
        assert all(review(case, member, mapping) in prompt for member in MODELS)
    if case.effective_mode == "tie_breaker":
        assert prompt.startswith("You are the Chairman resolving a DEADLOCKED deliberation.")
        assert "DECIDING VOTE" in prompt and '"deadlock_resolution"' in prompt
    elif case.mode == "binary":
        assert "BINARY VERDICT" in prompt and "DECIDING VOTE" not in prompt
    else:
        assert prompt.startswith("You are the Chairman of an LLM Council.")
        assert all(candidate(member) in prompt for member in case.members)
    return 401 if case.failure == "auth" else 200, case.final_content


def classify(case, request, *, question=QUESTION, title=TITLE):
    assert (request.method, str(request.url), request.headers.get("authorization")) == (
        "POST", "https://openrouter.ai/api/v1/chat/completions", f"Bearer {KEY}")
    body = json.loads(request.content)
    model, prompt = body["model"], body["messages"][-1]["content"]
    assert question in prompt
    if prompt.startswith("Generate a very short title"):
        assert model == TITLE_MODEL
        return "title", model, 200, title
    if prompt == question:
        assert model in MODELS
        return "stage1", model, 200 if model in case.members else 401, candidate(model)
    if "<responses_to_evaluate>" in prompt:
        assert model in MODELS and len(case.members) == len(MODELS)
        mapping = observe_mapping(prompt)
        if case.empty_reviews:
            return "stage2", model, 503, None
        return "stage2", model, 200, review(case, model, mapping)
    assert prompt.startswith("You are the Chairman")
    code, content = chairman_reply(case, model, prompt)
    return "stage3", model, code, content


def journal(stage, model, code):
    with Path(JOURNAL).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"stage": stage, "model": model, "status": code}) + "\n")


async def controlled_ui07_provider(_transport, request, *, question=QUESTION, title=TITLE):
    try:
        case = CASES[Path(SCENARIO).read_text(encoding="utf-8")]
        stage, model, code, content = classify(case, request, question=question, title=title)
    except (AssertionError, KeyError, ValueError, TypeError, IndexError, OSError):
        journal("rejected", "unknown", 0)
        raise AssertionError("UI07: unexpected provider request") from None
    journal(stage, model, code)
    if code != 200:
        return httpx.Response(code, json={"error": {"code": code, "message": "UI07_CONTROLLED_AUTH_FAILURE"}})
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0125}})


if __name__ == "__main__":
    if not os.environ.get("LLM_COUNCIL_UI_DATA_DIR") or not os.environ.get("LLM_COUNCIL_CONFIG"):
        raise RuntimeError("Run the isolated browser harness, not the server directly")
    serve(sys.argv[1], async_transport=controlled_ui07_provider)
