"""Offline guard against hiding the Free profile limitations after setup.

These checks keep the UI wiring and messaging consistent while the Vite build
validates the JSX structure in CI. They do not require a browser or paid API.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"


def test_free_banner_is_only_shown_when_saved_active_profile_is_free():
    app = (FRONTEND / "App.jsx").read_text(encoding="utf-8")
    chat = (FRONTEND / "components" / "ChatInterface.jsx").read_text(
        encoding="utf-8"
    )
    assert "setActiveProfile(settings.profile)" in app
    assert "onProfileChange={setActiveProfile}" in app
    assert "activeProfile={activeProfile}" in app
    assert "activeProfile === 'free'" in chat
    assert "View profiles" in chat
    assert "onClick={onOpenSettings}" in chat


def test_free_notice_is_accurate_and_available_during_selection():
    settings = (FRONTEND / "components" / "Settings.jsx").read_text(
        encoding="utf-8"
    )
    chat = (FRONTEND / "components" / "ChatInterface.jsx").read_text(
        encoding="utf-8"
    )
    assert "onProfileChange?.(data.profile)" in settings
    assert "isFree && (" in settings
    assert "three-stage Council still" in settings
    assert "optional rubric, bias," in settings
    assert "OpenRouter charges" in settings
    assert "may provide" in chat
    assert "OpenRouter charges" in chat
    assert "style normalization and extra evaluations" in chat
