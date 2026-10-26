"""Start and inspect the local Ollama service used by Neo."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)
OLLAMA_MODEL = "t-tech/T-lite-it-2.1:q4_K_M"


def _server_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434").strip()
    if "://" not in host:
        host = f"http://{host}"
    return host.rstrip("/")


def _read_tags(base_url: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=0.7) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def _find_ollama() -> str | None:
    executable = shutil.which("ollama")
    if executable:
        return executable
    if os.name == "nt":
        candidates = (
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
            os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "Ollama", "ollama.exe"),
        )
        return next((path for path in candidates if os.path.isfile(path)), None)
    return None


def ensure_ollama_running() -> subprocess.Popen | None:
    """Connect to Ollama or launch its local daemon without blocking app startup for long."""
    base_url = _server_url()
    tags = _read_tags(base_url)
    owned_process = None
    if tags is None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            logger.warning("Ollama недоступен по адресу %s.", base_url)
            return None
        executable = _find_ollama()
        if not executable:
            logger.warning("Ollama не найдена. Установите Ollama и повторно запустите Neo.")
            return None
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            owned_process = subprocess.Popen(
                [executable, "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except OSError:
            logger.exception("Не удалось запустить Ollama (%s serve).", executable)
            return None

        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            if owned_process.poll() is not None:
                logger.warning("Процесс Ollama завершился при запуске (код %s).", owned_process.returncode)
                return None
            tags = _read_tags(base_url)
            if tags is not None:
                break
            time.sleep(0.4)
        if tags is None:
            logger.warning("Ollama запущена, но не ответила на %s за 18 секунд.", base_url)
            return owned_process
        logger.info("Локальный сервер Ollama запущен Neo.")

    available = {
        str(model.get("name", "")).casefold()
        for model in (tags or {}).get("models", [])
    }
    if OLLAMA_MODEL.casefold() not in available:
        logger.warning(
            "Модель Neo %s не найдена в Ollama. Выполните: ollama pull %s",
            OLLAMA_MODEL,
            OLLAMA_MODEL,
        )
    else:
        logger.info("Модель Neo доступна в Ollama: %s", OLLAMA_MODEL)
    return owned_process
