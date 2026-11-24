"""Read-only Free catalogue discovery; no changes to Amiable or settings.

Catalogue membership is not proof of inference availability or account quota.
Run locally: python -m backend.free_model_catalog
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .free_profile import FREE_MODELS

MODELS_URL = "https://openrouter.ai/api/v1/models"
MIN_CONTEXT = 8192
MODEL_ID = re.compile(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+:free")


def _zero(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    try:
        return Decimal(str(value)) == 0
    except InvalidOperation:
        return False


def eligible_models(document: dict) -> list[str]:
    """Fail closed on missing/malformed pricing and require concrete text models."""
    if not isinstance(document, dict) or not isinstance(document.get("data"), list):
        raise ValueError("Invalid OpenRouter catalogue")
    candidates: set[str] = set()
    for item in document["data"]:
        if not isinstance(item, dict):
            continue
        slug = item.get("id")
        if not isinstance(slug, str) or not MODEL_ID.fullmatch(slug):
            continue
        if slug.lower().startswith("openrouter/"):
            continue
        pricing = item.get("pricing")
        if not isinstance(pricing, dict):
            continue
        if not all(_zero(pricing.get(key)) for key in ("prompt", "completion")):
            continue
        if not _zero(pricing.get("request", 0)):
            continue
        architecture = item.get("architecture")
        if not isinstance(architecture, dict):
            continue
        inputs = architecture.get("input_modalities")
        outputs = architecture.get("output_modalities")
        if not isinstance(inputs, list) or "text" not in inputs:
            continue
        if not isinstance(outputs, list) or "text" not in outputs:
            continue
        context = item.get("context_length")
        if type(context) is not int or context < MIN_CONTEXT:
            continue
        endpoints = item.get("endpoints")
        if endpoints is not None and (
            not isinstance(endpoints, list) or "/api/v1/chat/completions" not in endpoints
        ):
            continue
        candidates.add(slug)
    return sorted(candidates)


def select_pool(candidates: list[str], size: int = 3) -> list[str]:
    """Prefer existing models and distinct author namespaces, not fake quality scores."""
    if size < 1:
        raise ValueError("Pool size must be positive")
    unique = set(candidates)
    if len(unique) < size:
        raise ValueError(f"Only {len(unique)} eligible free models; need {size}")
    ordered = sorted(unique, key=lambda slug: (
        FREE_MODELS.index(slug) if slug in FREE_MODELS else len(FREE_MODELS),
        slug,
    ))
    selected: list[str] = []
    authors: set[str] = set()
    for slug in ordered:
        author = slug.split("/", 1)[0]
        if author not in authors:
            selected.append(slug)
            authors.add(author)
            if len(selected) == size:
                return selected
    return (selected + [slug for slug in ordered if slug not in selected])[:size]


def fetch_catalogue(client: httpx.Client) -> dict:
    """One metadata GET, never an inference call."""
    response = client.get(MODELS_URL, timeout=15.0)
    response.raise_for_status()
    return response.json()


def report(document: dict) -> dict:
    candidates = eligible_models(document)
    return {
        "eligible_models": candidates,
        "existing_models": {
            slug: slug in candidates for slug in FREE_MODELS
        },
        "proposed_pool": select_pool(candidates) if len(candidates) >= 3 else [],
        "inference_verified": False,
    }


def main() -> int:
    try:
        with httpx.Client(follow_redirects=False) as client:
            result = report(fetch_catalogue(client))
        print(json.dumps(result, indent=2))
        return 0 if result["proposed_pool"] else 2
    except httpx.HTTPStatusError as exc:
        print(f"Catalogue request failed: HTTP {exc.response.status_code}")
    except (httpx.HTTPError, ValueError):
        print("Catalogue request failed or returned invalid data.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
