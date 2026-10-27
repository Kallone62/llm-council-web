"""Small UI-only OpenRouter Free preset, preserving Amiable's four native tiers.

The pinned engine has four native tiers. Free is a distinct application preset
that changes only the active Council model/Chairman and Free safety options,
while preserving whichever native tier was already selected. In particular,
selecting Free does NOT select or redefine Quick. No engine fork or Ollama.
"""

from __future__ import annotations

from typing import Any

# Each new endpoint returned HTTP 200 with the user's OpenRouter key in an
# isolated free-only probe (October 28, 2026). Providers can change any time.
FREE_MODELS = (
    "nvidia/nemotron-3.5-super-vl:free",
    "dots-studio/dots-3-note-preview:free",
    "cohere/north-mini-code:free",
)
FREE_CHAIRMAN = FREE_MODELS[0]
FREE_SUMMARY_MODEL = FREE_MODELS[0]

# The previously distributed beta.2–beta.5 Free preset. Retain recognition
# only to let existing user settings upgrade safely; never query these models
# in a new default Free configuration.
LEGACY_FREE_MODELS = (
    "google/gemma-4-31b-it:free",
    "qwen/qwen3.8-27b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
)
LEGACY_FREE_CHAIRMAN = LEGACY_FREE_MODELS[0]


def is_free_profile(config: Any) -> bool:
    """Identify the persisted Free preset from Amiable's effective settings."""
    return (
        list(config.council.models) == list(FREE_MODELS)
        and config.council.chairman == FREE_CHAIRMAN
    )


def _saved_council(document: dict) -> dict:
    """Accept both Amiable's flat and legacy envelope YAML formats."""
    sections = document.get("council")
    if isinstance(sections, dict) and isinstance(sections.get("council"), dict):
        sections = sections["council"]
    return sections if isinstance(sections, dict) else {}


def is_legacy_free_document(document: dict) -> bool:
    """Recognize the old *exact* Free preset; never classify paid custom pools."""
    council = _saved_council(document)
    return (
        council.get("models") == list(LEGACY_FREE_MODELS)
        and council.get("chairman") == LEGACY_FREE_CHAIRMAN
    )


def is_free_document(document: dict) -> bool:
    """Detect Free intent in saved YAML, even across preset updates."""
    council = _saved_council(document)
    return (
        (council.get("models") == list(FREE_MODELS)
         and council.get("chairman") == FREE_CHAIRMAN)
        or is_legacy_free_document(document)
    )


def safe_local_title(query: str) -> str:
    """Free mode avoids Amiable's paid Gemini title model."""
    first = " ".join(query.split())[:50].strip()
    return first or "New Conversation"
