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

from .paths import configure_environment

# Должно выполниться до импорта ollama / huggingface_hub / torch (они читают окружение при импорте).
configure_environment()

import subprocess

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
from .ollama_runtime import ensure_ollama_running, stop_ollama
from .setup_runtime import is_setup_complete

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}/"


def _serve() -> None:
    # Импортируем приложение явно: строковый путь "module:app" PyInstaller не видит.
    from neo.backend.app.main import app as fastapi_app

    server = uvicorn.Server(
        uvicorn.Config(
            fastapi_app,
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


def _run_setup_process() -> bool:
    """Первичная настройка идёт в отдельном процессе: pywebview нельзя запускать дважды в одном."""
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--setup"]
    else:
        command = [sys.executable, "-m", "neo.desktop.launcher", "--setup"]
    return subprocess.run(command).returncode == 0


def main() -> None:
    if "--setup" in sys.argv[1:]:
        from .setup_window import run_setup_window

        sys.exit(0 if run_setup_window() else 1)

    if not is_setup_complete() and not _run_setup_process():
        return  # пользователь закрыл окно настройки

    ollama_process = ensure_ollama_running()
    try:
        _serve()
    finally:
        stop_ollama(ollama_process)  # иначе ollama.exe останется висеть после закрытия Neo


if __name__ == "__main__":
    main()
