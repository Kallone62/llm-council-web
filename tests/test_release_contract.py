"""Documented Windows beta publication must be intentional and consistent."""

from pathlib import Path

from backend import main


ROOT = Path(__file__).resolve().parents[1]


def test_beta_version_matches_download_and_notes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    notes = (ROOT / "RELEASE_NOTES_v1.1.0-beta.5.md").read_text(encoding="utf-8")
    assert main.APP_VERSION == "1.1.0-beta.5"
    assert "releases/tag/v1.1.0-beta.5" in readme
    assert "LLMCouncilWeb-Windows-x64.zip" in notes
    assert "OpenRouter API key" in notes
    assert "does not publish a clear license" in notes


def test_publisher_requires_specific_beta_marker_or_manual_dispatch():
    wf = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(
        encoding="utf-8"
    )
    assert "'[publish-beta-v1.1.0-beta.5]'" in wf
    assert "github.ref == 'refs/heads/main'" in wf
    assert "github.event_name == 'workflow_dispatch'" in wf
    assert "--prerelease" in wf
    assert "gh release create" in wf
    assert "RELEASE_NOTES_v1.1.0-beta.5.md" in wf
    assert "The original" not in wf  # notes are kept in versioned Markdown
