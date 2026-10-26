"""Offline speech recognition for Russian audio using faster-whisper."""

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

                # The multilingual base model is a practical starting point for
                # short commands and runs on CPU without a CUDA installation.
                _model = WhisperModel(
                    os.environ.get("NEO_WHISPER_MODEL", "small"),
                    device="cpu",
                    compute_type="int8",
                )
    return _model


def transcribe_audio(audio_path: str) -> str:
    """Transcribe a local audio file as Russian and return recognized text."""
    model = _get_model()
    options = {
        "language": "ru",
        "beam_size": 5,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        # Avoid initial_prompt here: Whisper can echo the prompt when the clip
        # is quiet or contains very short speech, returning it as a transcript.
    }
    with _inference_lock:
        segments, _info = model.transcribe(audio_path, vad_filter=True, **options)
        text = " ".join(segment.text.strip() for segment in segments).strip()

    # Very short commands or recordings clipped close to speech can be
    # mistaken for silence by VAD. Retry the same audio without that filter.
    if not text:
        with _inference_lock:
            segments, _info = model.transcribe(audio_path, vad_filter=False, **options)
            text = " ".join(segment.text.strip() for segment in segments).strip()

    logger.info("Transcribed Russian voice command (%d characters)", len(text))
    return text


def transcribe_samples(samples) -> str:
    """Recognize a float32 mono audio buffer sampled at 16 kHz."""
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
    """Correct known Russian ASR slips when the phrase is clearly a schedule command."""
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

    # Speech recognition can drop the preposition before a clock written as
    # "17.00". Normalize it to the unambiguous form understood downstream.
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
