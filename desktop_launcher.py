"""Standalone Windows entry point for the unchanged Karpathy UI + Amiable engine.

The executable bundles a production Vite build and Python dependencies, so
end users do not need Node.js, Python, Git, uv, or a second development server.
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORT = 8001
URL = f"http://localhost:{PORT}/"


def frontend_dist() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "frontend" / "dist"


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
    print(f"Could not open {URL}. Check the console for startup errors.", flush=True)


def _ensure_local_port_available() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
        try:
            check.bind((HOST, PORT))
        except OSError as exc:
            raise SystemExit(
                f"Port {PORT} is already in use. Close another LLM Council "
                "instance before starting this one."
            ) from exc


def main() -> None:
    from fastapi.staticfiles import StaticFiles

    built = frontend_dist()
    if not (built / "index.html").is_file():
        raise SystemExit(
            "The bundled React frontend is missing. "
            "Use the official release ZIP rather than running an isolated EXE."
        )

    _ensure_local_port_available()

    # Import only after the frozen-data path and executable location are set;
    # backend.config resolves the external YAML, .env and JSON storage at import.
    from backend.main import app
    import uvicorn

    # API routes are registered first. StaticFiles is mounted last, preserving
    # the existing REST/SSE interface and every React component unchanged.
    app.mount("/", StaticFiles(directory=str(built), html=True), name="frontend")

    print("LLM Council Web is starting...", flush=True)
    print(f"Open {URL} if the browser does not open automatically.", flush=True)
    print("Close this window to stop the local server.", flush=True)
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
