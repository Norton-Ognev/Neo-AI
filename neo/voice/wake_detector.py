"""Low-cost Russian wake phrase detector powered by Vosk."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

_MODEL_NAME = "vosk-model-small-ru-0.22"
_MODEL_URL = f"https://alphacephei.com/vosk/models/{_MODEL_NAME}.zip"
_WAKE_GRAMMAR = [
    "привет нео",
    "привет неон",
    "привет неос",
    "привет нио",
    "привет neo",
    "[unk]",
]


def _model_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return root / "Neo" / "models" / _MODEL_NAME


def _download_model_archive(archive_path: Path) -> None:
    request = urllib.request.Request(
        _MODEL_URL,
        headers={"User-Agent": "Neo-Assistant/1.0"},
    )
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response, archive_path.open("wb") as archive:
                while chunk := response.read(1024 * 1024):
                    archive.write(chunk)
            if archive_path.stat().st_size < 1_000_000 or not zipfile.is_zipfile(archive_path):
                raise RuntimeError("Сервер вернул неполный или повреждённый архив модели Vosk.")
            return
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            last_error = exc
            archive_path.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(1 + attempt * 2)

    manual_path = _model_directory()
    raise RuntimeError(
        "Не удалось скачать модель Vosk: сервер недоступен или TLS-соединение оборвалось. "
        f"Скачайте архив {_MODEL_URL}, распакуйте папку {_MODEL_NAME} в "
        f"{manual_path.parent} и повторно включите фоновое прослушивание."
    ) from last_error


def _ensure_model(on_status=None) -> Path:
    target = _model_directory()
    if (target / "am").is_dir() and (target / "conf").is_dir():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    if on_status:
        on_status()
    logger.info("Загружается локальная модель Vosk: %s", _MODEL_NAME)

    with tempfile.TemporaryDirectory(prefix="neo-vosk-") as temporary_directory:
        archive_path = Path(temporary_directory) / f"{_MODEL_NAME}.zip"
        _download_model_archive(archive_path)

        staging = Path(temporary_directory) / "extracted"
        staging.mkdir()
        root = staging.resolve()
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                destination = (staging / member.filename).resolve()
                if destination != root and root not in destination.parents:
                    raise RuntimeError("Архив модели Vosk содержит недопустимый путь.")
            archive.extractall(staging)

        extracted_model = staging / _MODEL_NAME
        if not (extracted_model / "am").is_dir() or not (extracted_model / "conf").is_dir():
            raise RuntimeError("В загруженном архиве не найдена корректная модель Vosk.")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(extracted_model), str(target))

    return target


def create_detector(sample_rate: int, on_status=None):
    """Load Vosk and return its recognizer restricted to wake phrase variants."""
    try:
        from vosk import Model, KaldiRecognizer, SetLogLevel
    except ImportError as exc:
        raise RuntimeError("Vosk не установлен. Установите зависимости Neo.") from exc

    SetLogLevel(-1)
    path = _ensure_model(on_status)
    model = Model(str(path))
    recognizer = KaldiRecognizer(model, float(sample_rate), json.dumps(_WAKE_GRAMMAR, ensure_ascii=False))
    recognizer.SetWords(False)
    return model, recognizer


def phrase_detected(text: str) -> bool:
    """Check recognized or partial text for a Russian wake phrase variant."""
    import re

    normalized = " ".join(text.lower().replace("ё", "е").split())
    return bool(re.search(r"\bпривет\s+(?:нео|неон|неос|нио|neo)\b", normalized))
