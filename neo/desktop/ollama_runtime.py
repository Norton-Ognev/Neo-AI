"""Start and inspect the local Ollama service used by Neo.

Отличия от прежней версии:
  * сначала ищется Ollama, встроенная в приложение (paths.find_bundled_ollama);
  * добавлена stop_ollama(), чтобы ollama.exe не оставался в памяти после выхода из Neo;
  * адрес/папка моделей берутся из OLLAMA_HOST / OLLAMA_MODELS, выставленных paths.configure_environment().
"""

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

from .paths import find_bundled_ollama

logger = logging.getLogger(__name__)
OLLAMA_MODEL = "t-tech/T-lite-it-2.1:q4_K_M"


def server_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434").strip()
    if "://" not in host:
        host = f"http://{host}"
    return host.rstrip("/")


def read_tags(base_url: str | None = None) -> dict | None:
    try:
        with urllib.request.urlopen(f"{base_url or server_url()}/api/tags", timeout=0.7) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def model_installed(tags: dict | None = None) -> bool:
    tags = tags if tags is not None else read_tags()
    available = {str(m.get("name", "")).casefold() for m in (tags or {}).get("models", [])}
    return OLLAMA_MODEL.casefold() in available


def _find_ollama() -> str | None:
    bundled = find_bundled_ollama()
    if bundled:
        return str(bundled)
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
    """Connect to Ollama or launch its local daemon. Returns the process we own (or None)."""
    base_url = server_url()
    tags = read_tags(base_url)
    owned_process = None
    if tags is None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            logger.warning("Ollama недоступен по адресу %s.", base_url)
            return None
        executable = _find_ollama()
        if not executable:
            logger.warning("Ollama не найдена.")
            return None
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            owned_process = subprocess.Popen(
                [executable, "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                env=os.environ.copy(),  # содержит OLLAMA_HOST / OLLAMA_MODELS
            )
        except OSError:
            logger.exception("Не удалось запустить Ollama (%s serve).", executable)
            return None

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if owned_process.poll() is not None:
                logger.warning("Процесс Ollama завершился при запуске (код %s).", owned_process.returncode)
                return None
            tags = read_tags(base_url)
            if tags is not None:
                break
            time.sleep(0.4)
        if tags is None:
            logger.warning("Ollama запущена, но не ответила на %s за 30 секунд.", base_url)
            return owned_process
        logger.info("Локальный сервер Ollama запущен Neo.")

    if model_installed(tags):
        logger.info("Модель Neo доступна в Ollama: %s", OLLAMA_MODEL)
    else:
        logger.warning("Модель Neo %s не найдена в Ollama.", OLLAMA_MODEL)
    return owned_process


def stop_ollama(process: subprocess.Popen | None) -> None:
    """Остановить Ollama вместе с дочерними runner-процессами (только если её запустили мы)."""
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
