"""Офлайн-распознавание русской речи с использованием faster-whisper."""

from __future__ import annotations

import logging
import os
import re
import threading

logger = logging.getLogger(__name__)
_model = None
_model_lock = threading.Lock()
_inference_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel

                # Многоязычная базовая модель — практичная отправная точка для
                # коротких команд; работает на CPU без установки CUDA.
                _model = WhisperModel(
                    os.environ.get("NEO_WHISPER_MODEL", "small"),
                    device="cpu",
                    compute_type="int8",
                )
    return _model


def transcribe_audio(audio_path: str) -> str:
    """Распознаёт локальный аудиофайл как русскую речь и возвращает распознанный текст."""
    model = _get_model()
    options = {
        "language": "ru",
        "beam_size": 5,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        # Здесь избегаем initial_prompt: Whisper может повторить промпт, если клип
        # тихий или содержит очень короткую речь, и вернуть его как транскрипт.
    }
    with _inference_lock:
        segments, _info = model.transcribe(audio_path, vad_filter=True, **options)
        text = " ".join(segment.text.strip() for segment in segments).strip()

    # Очень короткие команды или записи, обрезанные вплотную к речи, могут
    # быть приняты VAD за тишину. Повторяем тот же аудиофайл без этого фильтра.
    if not text:
        with _inference_lock:
            segments, _info = model.transcribe(audio_path, vad_filter=False, **options)
            text = " ".join(segment.text.strip() for segment in segments).strip()

    logger.info("Transcribed Russian voice command (%d characters)", len(text))
    return text


def transcribe_samples(samples) -> str:
    """Распознаёт монофонический аудиобуфер float32 с частотой дискретизации 16 кГц."""
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if audio.size < 4_000:
        return ""
    model = _get_model()
    with _inference_lock:
        segments, _info = model.transcribe(
            audio,
            language="ru",
            beam_size=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,
            no_speech_threshold=0.55,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


def normalize_transcript(text: str) -> str:
    """Исправляет известные ошибки русского ASR, когда фраза явно является командой расписания."""
    if not re.search(r"\b(?:запиши|запланируй|создай|добавь|поставь)\b", text, re.IGNORECASE):
        return text
    if not re.search(
        r"\b(?:завтра|сегодня|послезавтра|следующ(?:ий|его)\s+день|"
        r"через\s+(?:сутки|день)|(?:в|на)\s*\d{1,2}(?:[:.]\d{2})?)\b",
        text,
        re.IGNORECASE,
    ):
        return text
    text = re.sub(
        r"^(\s*(?:привет|здравствуй|здравствуйте)[\s,!]+)(?:неос|нео|нева)\b",
        r"\1Нео",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(?:с\s+азвон|со\s+звон|с\s+озвон)\b", "созвон", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:сазон|сазвон|созан)\b", "созвон", text, flags=re.IGNORECASE)

    # Распознавание речи может потерять предлог перед временем, записанным как
    # «17.00». Приводим его к однозначной форме, понятной дальше по обработке.
    def normalize_clock(match: re.Match[str]) -> str:
        prefix = match.group("prefix") or "в "
        return f"{prefix}{match.group('hour')}:{match.group('minute')}"

    text = re.sub(
        r"(?<!\w)(?P<prefix>(?:в|на)\s+)?(?P<hour>\d{1,2})[.,](?P<minute>\d{2})(?!\d)",
        normalize_clock,
        text,
        flags=re.IGNORECASE,
    )
    return text