"""Small UI-only OpenRouter Free preset, preserving Amiable's four native tiers.

The pinned engine has four native tiers. Free is a distinct application preset
that changes only the active Council model/Chairman and Free safety options,
while preserving whichever native tier was already selected. In particular,
selecting Free does NOT select or redefine Quick. No Free-specific engine fork or Ollama.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

import httpx

# Verified as :free endpoints on OpenRouter in October 2026.
FREE_MODELS = (
    "google/gemma-4-31b-it:free",
    "qwen/qwen3.8-27b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
)
FREE_CHAIRMAN = FREE_MODELS[0]
FREE_SUMMARY_MODEL = FREE_MODELS[0]


def is_free_profile(config: Any) -> bool:
    """Identify the persisted Free preset from Amiable's effective settings."""
    return (
        list(config.council.models) == list(FREE_MODELS)
        and config.council.chairman == FREE_CHAIRMAN
    )


def is_free_document(document: dict) -> bool:
    """Detect free intent from saved YAML even if an env override defeats it."""
    sections = document.get("council")
    # Old envelope syntax: top-level council contains a nested council block.
    if isinstance(sections, dict) and isinstance(sections.get("council"), dict):
        council = sections["council"]
    else:
        council = sections if isinstance(sections, dict) else {}
    return (
        council.get("models") == list(FREE_MODELS)
        and council.get("chairman") == FREE_CHAIRMAN
    )


def safe_local_title(query: str) -> str:
    """Free mode avoids Amiable's paid Gemini title model."""
    first = " ".join(query.split())[:50].strip()
    return first or "New Conversation"


OPENROUTER_CATALOG_URL = "https://openrouter.ai/api/v1/models"
_REQUIRED_PRICE_FIELDS = {"prompt", "completion", "request"}
_PRICE_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


class FreeCatalogError(Exception):
    """No trustworthy zero-price catalog entry for the whole Free preset."""


def _is_zero_price(value: Any) -> bool:
    # Do not coerce booleans, nulls, malformed strings or underflowed floats.
    if type(value) is str:
        if not _PRICE_NUMBER.fullmatch(value):
            return False
    elif type(value) not in (int, Decimal):
        return False
    try:
        price = Decimal(value)
        return price.is_finite() and price.is_zero()
    except (InvalidOperation, ValueError):
        return False


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate catalog JSON key")
        result[key] = value
    return result


def verify_free_catalog(api_key: str) -> None:
    """Fetch one fresh catalog for each Free round, before any inference.

    The exact preset remains fixed. Missing, duplicated, malformed or nonzero
    prices fail closed; unrelated paid entries are ignored. This preflight
    is not evidence of live account access or the eventual provider charge.
    """
    try:
        with httpx.Client(timeout=10.0, follow_redirects=False, trust_env=False) as client:
            response = client.get(OPENROUTER_CATALOG_URL,
                                  headers={"Authorization": f"Bearer {api_key}"})
            response.raise_for_status()
            payload = response.json(parse_float=Decimal, object_pairs_hook=_unique_json_object)
    except (httpx.HTTPError, ValueError, InvalidOperation) as exc:
        raise FreeCatalogError("Could not read the OpenRouter model catalog") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise FreeCatalogError("Invalid model catalog")
    required = {*FREE_MODELS, FREE_CHAIRMAN, FREE_SUMMARY_MODEL}
    verified = set()
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        model = entry["id"]
        if model not in required:
            continue
        pricing = entry.get("pricing")
        if (model in verified or not isinstance(pricing, dict)
                or not _REQUIRED_PRICE_FIELDS.issubset(pricing)
                or not all(_is_zero_price(value) for value in pricing.values())):
            raise FreeCatalogError("Configured Free model has unverified pricing")
        verified.add(model)
    if verified != required:
        raise FreeCatalogError("Configured Free model is missing from the catalog")
