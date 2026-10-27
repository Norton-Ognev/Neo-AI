"""Пути приложения и настройка окружения (вызывать ПЕРВЫМ делом в launcher).

Переменные окружения должны быть выставлены до импорта ollama / huggingface_hub / torch:
эти библиотеки читают их при импорте.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    """Папка данных пользователя. Совпадает с той, что уже использует Neo (БД, настройки, Vosk)."""
    root = os.environ.get("LOCALAPPDATA")
    return Path(root) / "Neo" if root else Path.home() / ".local" / "share" / "Neo"


def app_dir() -> Path:
    """Папка с NeoAI.exe (в режиме разработки — корень репозитория)."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def ollama_runtime_dir() -> Path:
    """Куда setup распаковывает Ollama, если она не положена установщиком."""
    return data_dir() / "runtime" / "ollama"


def find_bundled_ollama() -> Path | None:
    candidates = (
        app_dir() / "ollama" / "ollama.exe",       # положил установщик
        ollama_runtime_dir() / "ollama.exe",       # скачала первичная настройка
        app_dir() / "vendor" / "ollama" / "ollama.exe",  # режим разработки
    )
    return next((path for path in candidates if path.is_file()), None)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def uses_private_ollama() -> bool:
    """Своя Ollama (свой порт и папка моделей) — только в собранном exe или по NEO_PRIVATE_OLLAMA=1.

    Решение НЕ должно зависеть от того, лежит ли где-то ollama.exe: иначе оно меняется
    между процессами (setup / основной запуск) и модели оказываются в разных хранилищах.
    В режиме разработки используется обычная Ollama пользователя (~/.ollama).
    """
    return FROZEN or os.environ.get("NEO_PRIVATE_OLLAMA") == "1"


def models_store_id() -> str:
    """Идентификатор хранилища моделей, с которым работает этот запуск."""
    return os.environ.get("OLLAMA_MODELS") if uses_private_ollama() else "system"


def configure_environment() -> None:
    base = data_dir()
    os.environ.setdefault("HF_HOME", str(base / "hf"))                # faster-whisper
    os.environ.setdefault("TORCH_HOME", str(base / "torch"))          # Silero TTS (torch.hub)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    if uses_private_ollama():
        # Свой экземпляр: не конфликтует с Ollama пользователя (порт 11434, ~/.ollama).
        os.environ["OLLAMA_HOST"] = f"127.0.0.1:{_free_port()}"
        os.environ["OLLAMA_MODELS"] = str(base / "ollama" / "models")
