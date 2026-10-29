"""Офлайн-синтез русской речи с использованием Silero TTS и воспроизведения аудио в Windows."""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from xml.sax.saxutils import escape

from . import audio_devices

logger = logging.getLogger(__name__)
_speech_queue: queue.Queue[tuple[str | None, int]] = queue.Queue()
_worker_thread: threading.Thread | None = None
_start_lock = threading.Lock()
_state_lock = threading.Lock()
_playback_lock = threading.Lock()
_model_lock = threading.Lock()  # одна загрузка модели, даже если прогрев и первый ответ совпали
_synth_lock = threading.Lock()  # пробный синтез прогрева не должен идти параллельно с настоящим
_warmup_thread: threading.Thread | None = None
_speech_generation = 0
_model = None
_SAMPLE_RATE = 24_000
_SPEAKER = "eugene"
_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_DATE_ORDINALS = (
    "", "первое", "второе", "третье", "четвертое", "пятое", "шестое",
    "седьмое", "восьмое", "девятое", "десятое", "одиннадцатое",
    "двенадцатое", "тринадцатое", "четырнадцатое", "пятнадцатое",
    "шестнадцатое", "семнадцатое", "восемнадцатое", "девятнадцатое",
    "двадцатое", "двадцать первое", "двадцать второе", "двадцать третье",
    "двадцать четвертое", "двадцать пятое", "двадцать шестое",
    "двадцать седьмое", "двадцать восьмое", "двадцать девятое",
    "тридцатое", "тридцать первое",
)
_ONES_M = ("ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_ONES_F = ("ноль", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_TEENS = ("десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать")
_TENS = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто")
_HUNDREDS = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот")
_state = {
    "engine_ready": False,
    "speaking": False,
    "voice": f"Silero v4_ru / {_SPEAKER}",
    "warning": "При первом запуске модели нужно подключение к интернету для загрузки; затем она работает локально.",
    "last_error": None,
    "last_spoken_text": None,
}


def _cardinal(number: int, feminine: bool = False) -> str:
    """Произносит неотрицательное целое число по-русски для синтеза речи."""
    if number < 0:
        return "минус " + _cardinal(-number, feminine)
    if number == 0:
        return "ноль"
    if number >= 1_000_000:
        millions, remainder = divmod(number, 1_000_000)
        form = _plural_form(millions, "миллион", "миллиона", "миллионов")
        tail = _cardinal(remainder, feminine) if remainder else ""
        return " ".join(part for part in (_cardinal(millions), form, tail) if part)
    if number >= 1000:
        thousands, remainder = divmod(number, 1000)
        form = _plural_form(thousands, "тысяча", "тысячи", "тысяч")
        tail = _cardinal(remainder, feminine) if remainder else ""
        return " ".join(part for part in (_cardinal(thousands, feminine=True), form, tail) if part)

    parts = []
    hundreds, rest = divmod(number, 100)
    if hundreds:
        parts.append(_HUNDREDS[hundreds])
    if 10 <= rest < 20:
        parts.append(_TEENS[rest - 10])
    else:
        tens, ones = divmod(rest, 10)
        if tens:
            parts.append(_TENS[tens])
        if ones:
            parts.append((_ONES_F if feminine else _ONES_M)[ones])
    return " ".join(parts)


def _plural_form(number: int, one: str, few: str, many: str) -> str:
    last_two = number % 100
    if 11 <= last_two <= 14:
        return many
    last = number % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def _speech_ready_text(text: str) -> str:
    """Раскрывает русские даты, время и оставшиеся числительные перед Silero."""
    def date_to_words(match: re.Match[str]) -> str:
        day = int(match.group(1))
        month = _MONTHS.index(match.group(2).casefold()) + 1
        if 1 <= day < len(_DATE_ORDINALS):
            return f"{_DATE_ORDINALS[day]} {_MONTHS[month - 1]}"
        return match.group(0)

    text = re.sub(
        r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|"
        r"августа|сентября|октября|ноября|декабря)\b",
        date_to_words,
        text,
        flags=re.IGNORECASE,
    )

    def clock_to_words(match: re.Match[str]) -> str:
        hours = int(match.group("hour"))
        minutes = int(match.group("minute"))
        if hours > 23 or minutes > 59:
            return match.group(0)
        prefix = match.group("prefix") or ""
        hour_form = _plural_form(hours, "час", "часа", "часов")
        result = f"{prefix}{_cardinal(hours)} {hour_form}"
        if minutes:
            minute_form = _plural_form(minutes, "минута", "минуты", "минут")
            result += f" {_cardinal(minutes, feminine=True)} {minute_form}"
        return result

    text = re.sub(
        r"\b(?P<prefix>в\s+)?(?P<hour>\d{1,2}):(?P<minute>\d{2})\b",
        clock_to_words,
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\b\d+\b", lambda match: _cardinal(int(match.group(0))), text)


def _load_model():
    """Загружает русскую модель Silero один раз; Torch Hub кэширует её для офлайн-использования."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:  # пока ждали замок, модель мог загрузить прогрев
                import torch

                # Режим CPU сохраняет работоспособность на машинах без поддерживаемой GPU.
                torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
                model, _ = torch.hub.load(
                    "snakers4/silero-models",
                    "silero_tts",
                    language="ru",
                    speaker="v4_ru",
                    trust_repo=True,
                )
                model.to(torch.device("cpu"))
                _model = model  # публикуем только полностью готовую модель
    with _state_lock:
        _state["engine_ready"] = True
        _state["warning"] = None
        _state["last_error"] = None
    return _model


def _apply_tts(model, text: str):
    ssml_text = f'<speak><prosody rate="slow">{escape(text)}</prosody></speak>'
    with _synth_lock:
        return model.apply_tts(
            ssml_text=ssml_text,
            speaker=_SPEAKER,
            sample_rate=_SAMPLE_RATE,
        )


def warm_up() -> None:
    """Готовит голос заранее: тяжёлые импорты, загрузка модели и пробный синтез.

    Без этого всё это происходило при первом ответе: Ollama уже отвечала,
    а Silero ещё подгружалась. Первый вызов apply_tts тоже медленнее последующих,
    поэтому после загрузки делаем короткий пробный синтез (звук не воспроизводится).
    """
    import numpy  # noqa: F401  (предзагрузка)

    try:
        import sounddevice  # noqa: F401  (подгружает PortAudio)
    except Exception:  # noqa: BLE001 — нет аудиорантайма: модель всё равно греем
        logger.warning("sounddevice недоступен при прогреве TTS", exc_info=True)

    model = _load_model()
    _apply_tts(model, "Привет.")


def _warmup_worker() -> None:
    started = time.monotonic()
    try:
        warm_up()
    except Exception as exc:  # noqa: BLE001
        # Например, первый запуск без интернета. Не страшно: модель загрузится
        # при первом ответе, как раньше, а ошибку покажет get_status().
        with _state_lock:
            _state["last_error"] = f"{type(exc).__name__}: {exc}"
            _state["warning"] = "Не удалось заранее загрузить голосовую модель; загрузится при первом ответе."
        logger.warning("Прогрев Silero TTS не удался", exc_info=True)
    else:
        logger.info("Silero TTS прогрет за %.1f с", time.monotonic() - started)


def start_warmup() -> threading.Thread:
    """Запускает прогрев голоса в фоне (не блокирует запуск API). Повторный вызов безопасен."""
    global _warmup_thread
    with _start_lock:
        if _warmup_thread is None or not _warmup_thread.is_alive():
            _warmup_thread = threading.Thread(
                target=_warmup_worker, daemon=True, name="neo-silero-tts-warmup"
            )
            _warmup_thread.start()
        return _warmup_thread


def _synthesize_and_play(text: str, generation: int) -> bool:
    import numpy as np
    import sounddevice as sd

    with _playback_lock:
        if generation != _speech_generation:
            return False
    model = _load_model()
    # Silero v4 поддерживает управление просодией через SSML; замедление всего
    # ответа делает короткие подтверждения понятнее, не меняя выбранный голос.
    audio = _apply_tts(model, text)
    with _playback_lock:
        if generation != _speech_generation:
            return False
    samples = audio.detach().cpu().numpy().reshape(-1)
    samples = np.clip(samples * audio_devices.get_volume(), -1.0, 1.0)
    with _playback_lock:
        if generation != _speech_generation:
            return False
        sd.play(samples, samplerate=_SAMPLE_RATE, device=audio_devices.selected_device("output"))
    sd.wait()
    return True


def _worker() -> None:
    while True:
        text, generation = _speech_queue.get()
        try:
            if text is None:
                return
            with _playback_lock:
                if generation != _speech_generation:
                    continue
                with _state_lock:
                    _state["speaking"] = True
            played = _synthesize_and_play(text, generation)
            if played:
                with _state_lock:
                    _state["last_spoken_text"] = text
        except Exception as exc:
            with _state_lock:
                _state["engine_ready"] = False
                _state["last_error"] = f"{type(exc).__name__}: {exc}"
                _state["warning"] = "Не удалось синтезировать или воспроизвести речь. Подробности в last_error."
            logger.exception("Не удалось озвучить ответ Neo через Silero TTS")
        finally:
            with _state_lock:
                _state["speaking"] = False
            _speech_queue.task_done()


def _ensure_worker() -> None:
    global _worker_thread
    with _start_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(
                target=_worker, daemon=True, name="neo-silero-tts-worker"
            )
            _worker_thread.start()


def speak_text(text: str) -> None:
    """Ставит ответ в очередь на локальный синтез и воспроизведение, не блокируя API."""
    clean_text = " ".join(text.replace("•", " ").split())
    if not clean_text:
        return
    clean_text = _speech_ready_text(clean_text)
    _ensure_worker()
    with _playback_lock:
        _speech_queue.put((clean_text, _speech_generation))


def stop_speech() -> None:
    """Останавливает текущее воспроизведение и отбрасывает все ответы, которые ещё не начались."""
    global _speech_generation
    with _playback_lock:
        _speech_generation += 1
        while True:
            try:
                _speech_queue.get_nowait()
            except queue.Empty:
                break
            else:
                _speech_queue.task_done()
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            # Активного потока нет (или аудиорантайм не установлен) — всё уже остановлено.
            pass
        with _state_lock:
            _state["speaking"] = False


def get_status() -> dict:
    """Возвращает диагностику речевого движка и его очереди задач."""
    with _state_lock:
        return {
            **_state,
            "queued_items": _speech_queue.qsize(),
            "worker_running": bool(_worker_thread and _worker_thread.is_alive()),
            "warming_up": bool(_warmup_thread and _warmup_thread.is_alive()),
        }


def wait_until_done() -> None:
    """Ждёт, пока завершится вся поставленная в очередь речь (в основном полезно в демо)."""
    _speech_queue.join()