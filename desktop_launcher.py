"""Quiet Windows desktop launcher for the original UI + Amiable Council engine.

The frozen EXE has the GUI subsystem (no terminal). Run the unmodified
production React UI and FastAPI on loopback and manage lifetime from a tray
icon. Logs are saved in the user's local application data folder.
"""

from __future__ import annotations

import ctypes
from datetime import date
import multiprocessing
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORT = 8001
URL = f"http://localhost:{PORT}/app/"


def frontend_dist() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "frontend" / "dist"


def _log_path() -> Path:
    """Writable logs must not depend on the executable installation directory."""
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    log_dir = base / "LLMCouncilWeb" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"council-{date.today().isoformat()}.log"


def _configure_gui_logging() -> Path:
    path = _log_path()
    # PyInstaller --windowed sets stdout/stderr to None; uvicorn and libraries
    # still need valid streams for logging and traceback reporting.
    stream = path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    print(f"Starting LLM Council Web; log: {path}", flush=True)
    return path


def _message_box(message: str, *, error: bool = False) -> None:
    """Show startup errors without relying on a console or Tk installation."""
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(
            None,
            message,
            "LLM Council Web",
            0x10 if error else 0x40,
        )
    else:
        print(message, file=sys.stderr)


def _open_browser_when_ready() -> None:
    for _ in range(80):
        try:
            with urllib.request.urlopen(URL, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(URL)
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    print(f"Could not open {URL}. Open it manually in your browser.", flush=True)


def _ensure_local_port_available() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
        try:
            check.bind((HOST, PORT))
        except OSError as exc:
            raise RuntimeError(
                f"Port {PORT} is already in use. Close the other LLM Council "
                "instance from its tray icon, then try again."
            ) from exc


def _start_tray(server):
    """Windows system tray: open the app or exit without a terminal window."""
    import pystray
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (64, 64), "#192638")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, 57, 57), radius=13, fill="#31527b")
    for x, color in [(20, "#8ef6d9"), (32, "#ffffff"), (44, "#8ef6d9")]:
        draw.ellipse((x - 6, 25, x + 6, 37), fill=color)

    def open_app(_icon, _item):
        webbrowser.open(URL)

    def quit_app(icon, _item):
        server.should_exit = True
        icon.stop()

    icon = pystray.Icon(
        "LLMCouncilWeb",
        image,
        "LLM Council Web",
        menu=pystray.Menu(
            pystray.MenuItem("Open LLM Council", open_app, default=True),
            pystray.MenuItem("Quit LLM Council", quit_app),
        ),
    )
    icon.run_detached()
    return icon


def main() -> None:
    from fastapi.staticfiles import StaticFiles

    built = frontend_dist()
    if not (built / "index.html").is_file():
        raise RuntimeError(
            "The React frontend files are missing. Extract the complete "
            "release ZIP, including the _internal folder, before launching."
        )

    _ensure_local_port_available()

    # Import after the frozen-data path and executable location are set;
    # backend.config resolves the external YAML, .env and JSON storage then.
    from backend.main import app
    import uvicorn

    # Preserve the existing FastAPI API routes and SSE engine pipeline.
    app.mount("/app", StaticFiles(directory=str(built), html=True), name="frontend")

    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="info"))
    tray = None
    try:
        # Skip the tray only in explicitly headless automated smoke tests.
        if os.getenv("LLM_COUNCIL_NO_TRAY") != "1":
            tray = _start_tray(server)
        if os.getenv("LLM_COUNCIL_NO_BROWSER") != "1":
            threading.Thread(target=_open_browser_when_ready, daemon=True).start()
        print(f"Serving LLM Council Web at {URL}", flush=True)
        server.run()
    finally:
        if tray is not None:
            tray.stop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    log_path = None
    try:
        log_path = _configure_gui_logging()
        main()
    except Exception as exc:
        traceback.print_exc()
        details = f"\n\nDetails saved to:\n{log_path}" if log_path else ""
        _message_box(
            f"LLM Council Web could not start.\n\n{exc}{details}",
            error=True,
        )
        sys.exit(1)
