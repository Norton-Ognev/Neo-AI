"""Первичная настройка: Ollama-рантайм, LLM, Vosk, Whisper, Silero.

Все шаги идемпотентны и докачиваются при повторном запуске.
Тяжёлые библиотеки импортируются внутри функций, чтобы проверка is_setup_complete() была мгновенной.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .ollama_runtime import OLLAMA_MODEL, ensure_ollama_running, model_installed, read_tags, stop_ollama
from .paths import data_dir, find_bundled_ollama, models_store_id, ollama_runtime_dir, uses_private_ollama

logger = logging.getLogger(__name__)

SETUP_VERSION = 1          # увеличьте, если сменили модель/набор компонентов — настройка пройдёт заново
REQUIRED_FREE_GB = 12      # LLM ~5 ГБ + Whisper + Silero + рантайм Ollama + запас (уточните под свою модель)
# Для воспроизводимых релизов замените latest на конкретный тег, например .../download/v0.X.Y/...
OLLAMA_ZIP_URL = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"

Report = Callable[[str, float | None], None]   # (сообщение, общий прогресс 0..100 или None)


def _state_file() -> Path:
    return data_dir() / "setup_state.json"


def is_setup_complete() -> bool:
    try:
        state = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    # Настройка, сделанная для другого хранилища моделей (например, из режима разработки
    # с системной Ollama), для этого запуска недействительна.
    return state.get("version") == SETUP_VERSION and state.get("models_store") == models_store_id()


def _mark_complete() -> None:
    _state_file().parent.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(
        json.dumps({"version": SETUP_VERSION, "models_store": models_store_id()}),
        encoding="utf-8",
    )


# ---------- шаги ----------

def _step_ollama_runtime(report: Callable[[str, float | None], None]) -> None:
    if not uses_private_ollama() or find_bundled_ollama():
        return  # в режиме разработки работаем с установленной Ollama
    target = ollama_runtime_dir()
    target.mkdir(parents=True, exist_ok=True)
    report("Загрузка движка Ollama…", 0)
    with tempfile.TemporaryDirectory(prefix="neo-ollama-") as tmp:
        archive = Path(tmp) / "ollama.zip"
        request = urllib.request.Request(OLLAMA_ZIP_URL, headers={"User-Agent": "Neo-Assistant/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
                done += len(chunk)
                if total:
                    report("Загрузка движка Ollama…", done / total * 90)
        report("Распаковка Ollama…", 92)
        root = target.resolve()
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                dest = (target / member.filename).resolve()
                if dest != root and root not in dest.parents:
                    raise RuntimeError("Архив Ollama содержит недопустимый путь.")
            zf.extractall(target)
    if not (target / "ollama.exe").is_file():
        raise RuntimeError("В архиве Ollama не найден ollama.exe.")


def _step_llm(report: Callable[[str, float | None], None]) -> None:
    process = ensure_ollama_running()
    try:
        if read_tags() is None:
            raise RuntimeError("Не удалось запустить встроенную Ollama.")
        if model_installed():
            return
        import ollama

        client = ollama.Client()  # адрес берётся из OLLAMA_HOST
        for progress in client.pull(OLLAMA_MODEL, stream=True):
            pct = None
            if getattr(progress, "total", None) and getattr(progress, "completed", None):
                pct = progress.completed / progress.total * 100
            report(f"Загрузка языковой модели… {progress.status or ''}".strip(), pct)
    finally:
        stop_ollama(process)  # основной запуск стартует её заново


def _step_vosk(report: Callable[[str, float | None], None]) -> None:
    from neo.voice.wake_detector import _ensure_model

    report("Загрузка модели «привет, Нео»…", None)
    _ensure_model()


def _step_whisper(report: Callable[[str, float | None], None]) -> None:
    from faster_whisper import download_model

    report("Загрузка модели распознавания речи…", None)
    download_model(os.environ.get("NEO_WHISPER_MODEL", "small"))


def _step_silero(report: Callable[[str, float | None], None]) -> None:
    from neo.voice.tts import _load_model

    report("Загрузка голоса…", None)
    _load_model()


# (название, функция, доля общего прогресса)
STEPS = (
    ("Движок Ollama", _step_ollama_runtime, 6),
    ("Языковая модель", _step_llm, 66),
    ("Wake-word (Vosk)", _step_vosk, 3),
    ("Распознавание речи (Whisper)", _step_whisper, 15),
    ("Синтез речи (Silero)", _step_silero, 10),
)


def check_disk_space() -> None:
    base = data_dir()
    base.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(base).free / 1024**3
    if free_gb < REQUIRED_FREE_GB:
        raise RuntimeError(
            f"Недостаточно места на диске: свободно {free_gb:.1f} ГБ, нужно около {REQUIRED_FREE_GB} ГБ."
        )


def run_setup(report: Report) -> None:
    check_disk_space()
    total_weight = sum(weight for *_rest, weight in STEPS)
    finished = 0.0
    for name, step, weight in STEPS:
        def step_report(message: str, local: float | None, _base=finished, _w=weight) -> None:
            overall = (_base + _w * (local or 0) / 100) / total_weight * 100
            report(f"{name}: {message}", overall if local is not None else None)

        logger.info("Setup: %s", name)
        step(step_report)
        finished += weight
        report(f"{name}: готово", finished / total_weight * 100)
    _mark_complete()
    report("Готово", 100)
