"""Small UI-only OpenRouter Free preset, preserving Amiable's four native tiers.

The pinned engine does not have a 'free' tier. This maps the local UI preset to
the native 'quick' tier with *explicit* zero-priced OpenRouter endpoints for
all model calls. No Ollama dependency or engine rewrite is involved.
"""

from __future__ import annotations

from typing import Any

# Verified as :free endpoints on OpenRouter in October 2026.
FREE_MODELS = (
    "google/gemma-4-31b-it:free",
    "qwen/qwen3.8-27b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
)
FREE_CHAIRMAN = FREE_MODELS[0]
FREE_SUMMARY_MODEL = FREE_MODELS[0]
NATIVE_FREE_TIER = "quick"


def is_free_profile(config: Any) -> bool:
    """Identify the persisted Free preset from Amiable's effective settings."""
    return (
        config.tiers.default == NATIVE_FREE_TIER
        and list(config.council.models) == list(FREE_MODELS)
        and config.council.chairman == FREE_CHAIRMAN
    )


def is_free_document(document: dict) -> bool:
    """Detect free intent from saved YAML even if an env override defeats it."""
    sections = document.get("council")
    # Old envelope syntax: top-level council contains a nested council block.
    if isinstance(sections, dict) and isinstance(sections.get("council"), dict):
        tiers = sections.get("tiers", {})
        council = sections["council"]
    else:
        tiers = document.get("tiers", {})
        council = sections if isinstance(sections, dict) else {}
    return (
        isinstance(tiers, dict)
        and tiers.get("default") == NATIVE_FREE_TIER
        and council.get("models") == list(FREE_MODELS)
        and council.get("chairman") == FREE_CHAIRMAN
    )


def safe_local_title(query: str) -> str:
    """Free mode avoids Amiable's paid Gemini title model."""
    first = " ".join(query.split())[:50].strip()
    return first or "New Conversation"
