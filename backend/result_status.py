"""Translate native engine results into truthful application-level statuses."""
from __future__ import annotations

from typing import Any, Mapping


def final_error(result: Any) -> str | None:
    """Recognize both current and legacy persisted failure placeholders."""
    if not isinstance(result, Mapping):
        return "empty_synthesis"
    if result.get("error_status"):
        return str(result["error_status"])
    text = result.get("response")
    if result.get("model") == "error":
        return "no_responses" if isinstance(text, str) and "all models failed to respond" in text.lower() else "error"
    if not isinstance(text, str) or not text.strip():
        return "empty_synthesis"
    if text.lstrip().lower().startswith("error: unable to generate final synthesis"):
        return "error"
    return None


def normalize_final(result: Any) -> dict:
    error = final_error(result)
    output = dict(result) if isinstance(result, Mapping) else {}
    if error:
        output["error_status"] = error
        if error == "empty_synthesis":
            output["response"] = "Chairman returned no usable final synthesis."
    return output


def valid_review_count(reviews: list) -> int:
    """Native reviews can contain nonempty prose but no parsed ranking."""
    count = 0
    for review in reviews:
        if not isinstance(review, Mapping):
            continue
        parsed = review.get("parsed_ranking")
        if isinstance(parsed, Mapping):
            ranking = parsed.get("ranking")
        elif isinstance(parsed, list):
            ranking = parsed
        else:
            ranking = None
        if isinstance(ranking, list) and any(isinstance(item, str) and item.strip() for item in ranking):
            count += 1
    return count


def classify_round(stage1: list, stage2: list, stage3: dict, metadata: dict) -> dict:
    """Keep useful partial output, without claiming full peer-reviewed success."""
    output = dict(metadata or {})
    config = output.get("config") or {}
    expected = config.get("council_size")
    warnings = []
    if not stage1 or final_error(stage3):
        status = "failed"
        output.pop("verdict", None)
        if "quality_metrics" in output:
            output["quality_metrics"] = None
    else:
        if len(stage1) == 1:
            warnings.append("Only one model responded; peer review was skipped.")
        elif len(stage1) == 2:
            warnings.append("Only two models responded; peer review is degraded.")
        if isinstance(expected, int) and len(stage1) < expected:
            warnings.append(f"Received {len(stage1)} of {expected} individual responses.")
        if len(stage1) > 1:
            valid = valid_review_count(stage2)
            if not valid:
                warnings.append("No usable peer rankings; the final answer was not peer-validated.")
            elif valid < len(stage2):
                warnings.append(f"Only {valid} of {len(stage2)} returned reviews contained usable rankings.")
            reviewers = config.get("max_reviewers")
            if isinstance(expected, int):
                planned = min(expected, reviewers) if isinstance(reviewers, int) else expected
                if valid and valid < planned:
                    warnings.append(f"Received {valid} usable rankings out of up to {planned} reviewers.")
        if stage3.get("verdict_parse_error"):
            warnings.append("Chairman decision could not be parsed reliably.")
        if stage3.get("chairman_disabled"):
            warnings.append("Chairman synthesis was disabled; a model response is shown instead.")
        status = "partial" if warnings or output.get("status") == "partial" else "complete"
    output["status"] = status
    output["ui_warnings"] = warnings
    return output
