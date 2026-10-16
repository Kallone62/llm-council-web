"""Ensure tests do not depend on the default profile shipped to new users.

Existing integration tests intentionally mock paid-model calls. Their old
baseline was Amiable High; the application now ships Free for first installs.
Each test starts with its own isolated High YAML unless it explicitly chooses
Free, so offline tests don't require a real OpenRouter API key.
"""

import os
import pytest

from llm_council.unified_config import reload_config


@pytest.fixture(autouse=True)
def isolate_settings_for_tests(tmp_path, monkeypatch):
    old_config = os.environ.get("LLM_COUNCIL_CONFIG")
    config = tmp_path / "test-default.yaml"
    config.write_text("tiers:\n  default: high\n", encoding="utf-8")
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(config))
    reload_config()
    yield
    if old_config is None:
        monkeypatch.delenv("LLM_COUNCIL_CONFIG", raising=False)
    else:
        monkeypatch.setenv("LLM_COUNCIL_CONFIG", old_config)
    reload_config()
