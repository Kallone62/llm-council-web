"""Application-level configuration helpers.

Council behavior itself remains owned by Amiable's ``llm_council`` package.
This module only pins the local app paths and persists the small set of engine
settings exposed by the Karpathy UI.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_COUNCIL_CONFIG_PATH = PROJECT_ROOT / "llm_council.yaml"

# Load secrets regardless of the shell's current working directory.
load_dotenv(ENV_PATH)

# Keep this local application's config deterministic. A user-supplied
# LLM_COUNCIL_CONFIG still wins if they explicitly set one before startup.
os.environ.setdefault("LLM_COUNCIL_CONFIG", str(DEFAULT_COUNCIL_CONFIG_PATH))

# Karpathy UI conversation storage only.
DATA_DIR = os.getenv("LLM_COUNCIL_UI_DATA_DIR", str(PROJECT_ROOT / "data" / "conversations"))


def get_council_config_path() -> Path:
    """Return the active Amiable YAML path used by this app."""

    raw = os.getenv("LLM_COUNCIL_CONFIG")
    if not raw:
        return DEFAULT_COUNCIL_CONFIG_PATH
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def read_council_config_document() -> Dict[str, Any]:
    """Read the current YAML document without expanding secrets."""

    path = get_council_config_path()
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("llm_council.yaml must contain a YAML mapping")
    return data


def writable_config_sections(document: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return ``(sections, council_section)`` for flat or envelope YAML shapes.

    Amiable supports both a flat config and an older envelope where all
    UnifiedConfig sections live under a top-level ``council`` key. Supporting
    both here lets the Settings UI edit a user-supplied config without
    converting or flattening unrelated fields.
    """

    council_block = document.get("council")
    unified_section_names = {
        "tiers",
        "triage",
        "gateways",
        "credentials",
        "observability",
        "webhooks",
        "model_intelligence",
        "frontier",
        "evaluation",
        "secrets",
        "timeouts",
        "cache",
        "telemetry",
    }

    if isinstance(council_block, dict) and any(key in unified_section_names for key in council_block):
        sections = council_block
        inner_council = sections.setdefault("council", {})
    else:
        sections = document
        inner_council = sections.setdefault("council", {})

    if not isinstance(inner_council, dict):
        raise ValueError("The council configuration section must be a mapping")
    return sections, inner_council


def write_council_config_document(document: Dict[str, Any]) -> Path:
    """Atomically-ish persist YAML to the active config path."""

    path = get_council_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(document, handle, sort_keys=False, allow_unicode=True)
    tmp_path.replace(path)
    return path


def set_env_value(name: str, value: str) -> None:
    """Set or clear a single value in the local .env file and current process."""

    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    prefix = f"{name}="
    replacement = f"{name}={value}" if value else None
    output = []
    replaced = False

    for line in lines:
        if line.startswith(prefix):
            if not replaced and replacement is not None:
                output.append(replacement)
            replaced = True
        else:
            output.append(line)

    if not replaced and replacement is not None:
        if output and output[-1] != "":
            output.append("")
        output.append(replacement)

    ENV_PATH.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")

    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)
