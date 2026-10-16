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


def test_tray_open_and_quit_actions_work_without_cmd(monkeypatch):
    callbacks = {}
    state = {"opened": None, "ran": False, "stopped": False}

    class FakeDraw:
        def rounded_rectangle(self, *_args, **_kwargs):
            pass

        def ellipse(self, *_args, **_kwargs):
            pass

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
        Image=SimpleNamespace(new=lambda *_args, **_kwargs: object()),
        ImageDraw=SimpleNamespace(Draw=lambda _img: FakeDraw()),
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
