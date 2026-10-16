"""Desktop shell tests; no GUI, external services or provider API requests."""

from pathlib import Path
import sys
from types import SimpleNamespace

import desktop_launcher


def test_desktop_log_path_is_user_writable(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = desktop_launcher._log_path()
    assert path.parent == tmp_path / "LLMCouncilWeb" / "logs"
    assert path.suffix == ".log"
    assert path.parent.is_dir()


def test_tray_open_and_quit_actions_work_without_cmd(monkeypatch, tmp_path):
    callbacks = {}
    state = {"opened": None, "ran": False, "stopped": False, "logo_used": None}

    logo = tmp_path / "brand" / "icon-64.png"
    logo.parent.mkdir()
    logo.write_bytes(b"SVG-derived image")
    monkeypatch.setattr(desktop_launcher.sys, "_MEIPASS", str(tmp_path), raising=False)

    class FakePng:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def convert(self, mode):
            assert mode == "RGBA"
            return "branded tray icon"

    def fake_open(path):
        state["logo_used"] = path
        return FakePng()

    class FakeTrayIcon:
        def __init__(self, name, image, title, menu):
            assert name == "LLMCouncilWeb"
            assert title == "LLM Council Web"
            assert len(menu) == 2
            assert "Open LLM Council" in callbacks
            assert "Quit LLM Council" in callbacks

        def run_detached(self):
            state["ran"] = True

        def stop(self):
            state["stopped"] = True

    fake_pystray = SimpleNamespace(
        Icon=FakeTrayIcon,
        Menu=lambda *items: items,
        MenuItem=lambda name, callback, **kwargs: (
            callbacks.setdefault(name, callback), name
        )[1],
    )
    fake_pil = SimpleNamespace(
        Image=SimpleNamespace(open=fake_open),
    )
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setattr(
        desktop_launcher.webbrowser, "open",
        lambda url: state.__setitem__("opened", url),
    )

    server = SimpleNamespace(should_exit=False)
    icon = desktop_launcher._start_tray(server)
    assert state["ran"] is True
    assert state["logo_used"] == logo
    callbacks["Open LLM Council"](icon, None)
    assert state["opened"] == "http://localhost:8001/app/"
    callbacks["Quit LLM Council"](icon, None)
    assert server.should_exit is True
    assert state["stopped"] is True


def test_windows_release_workflow_builds_gui_subsystem():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "windows-release.yml").read_text(
        encoding="utf-8"
    )
    assert "PyInstaller --noconfirm --clean --onedir --windowed" in workflow
    assert "Assert EXE is a Windows GUI application (no console)" in workflow
    assert "LLM_COUNCIL_NO_TRAY: '1'" in workflow


def test_original_svg_is_used_in_browser_and_windows_build():
    root = Path(__file__).resolve().parents[1]
    source = (root / "frontend" / "public" / "llm-council.svg").read_text(
        encoding="utf-8"
    )
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    sidebar = (root / "frontend" / "src" / "components" / "Sidebar.jsx").read_text(
        encoding="utf-8"
    )
    workflow = (root / ".github" / "workflows" / "windows-release.yml").read_text(
        encoding="utf-8"
    )
    assert 'viewBox="0 0 256 256"' in source
    assert 'fill="url(#core)"' in source
    assert '%BASE_URL%llm-council.svg' in html
    assert 'llm-council.svg' in sidebar
    assert '--icon "build/brand/LLMCouncilWeb.ico"' in workflow
    assert '--add-data "build/brand/icon-64.png;brand"' in workflow
    assert 'scripts/build_desktop_icon.py' in workflow
