"""Run the local Neo API and open its interface in a desktop window."""

from __future__ import annotations

import faulthandler
import os
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import uvicorn
import webview

_CRASH_LOG = None
try:
    _log_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Neo" / "logs"
    _log_root.mkdir(parents=True, exist_ok=True)
    _CRASH_LOG = (_log_root / "neo-crash.log").open("ab", buffering=0)
    faulthandler.enable(file=_CRASH_LOG, all_threads=True)
except OSError:
    _CRASH_LOG = None


def _write_crash_record(title: str, exc_type, exc_value, exc_traceback) -> None:
    if _CRASH_LOG is None:
        return
    timestamp = datetime.now().astimezone().isoformat()
    stack = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    record = f"\n--- {title} · {timestamp} ---\n{stack}\n"
    try:
        _CRASH_LOG.write(record.encode("utf-8", errors="replace"))
    except OSError:
        pass


def _handle_main_exception(exc_type, exc_value, exc_traceback) -> None:
    _write_crash_record("Unhandled main-thread exception", exc_type, exc_value, exc_traceback)
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


def _handle_thread_exception(args) -> None:
    _write_crash_record(
        f"Unhandled exception in thread {args.thread.name!r}",
        args.exc_type,
        args.exc_value,
        args.exc_traceback,
    )
    threading.__excepthook__(args)


import sys

sys.excepthook = _handle_main_exception
threading.excepthook = _handle_thread_exception

from .audio_bridge import AudioBridge
from .ollama_runtime import ensure_ollama_running

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}/"


def main() -> None:
    ensure_ollama_running()
    server = uvicorn.Server(
        uvicorn.Config(
            "neo.backend.app.main:app",
            host=HOST,
            port=PORT,
            log_level="info",
            access_log=False,
        )
    )
    server_thread = threading.Thread(target=server.run, name="neo-api", daemon=True)
    server_thread.start()

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not server_thread.is_alive():
            raise RuntimeError("Не удалось запустить локальный сервер Neo на порту 8000.")
        try:
            with urllib.request.urlopen(URL + "health", timeout=1) as response:
                if response.status == 200:
                    break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    else:
        server.should_exit = True
        raise TimeoutError("Локальный сервер Neo не запустился за 30 секунд.")

    try:
        bridge = AudioBridge()
        window = webview.create_window(
            title="Neo — голосовой помощник",
            url=URL,
            width=1100,
            height=760,
            min_size=(720, 560),
            resizable=True,
            js_api=bridge,
        )
        bridge.bind_window(window)
        window.events.closing += bridge.shutdown
        webview.start()
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)


if __name__ == "__main__":
    main()
