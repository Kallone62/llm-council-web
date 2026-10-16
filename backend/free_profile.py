"""Small UI-only OpenRouter Free preset, preserving Amiable's four native tiers.

The pinned engine has four native tiers. Free is a distinct application preset
that changes only the active Council model/Chairman and Free safety options,
while preserving whichever native tier was already selected. In particular,
selecting Free does NOT select or redefine Quick. No engine fork or Ollama.
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
