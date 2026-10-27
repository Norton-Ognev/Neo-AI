"""Локальный непрерывный слушатель русской фразы активации для десктопного приложения."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from typing import Callable

from . import audio_devices

logger = logging.getLogger(__name__)
_COMMAND_END_SILENCE_SECONDS = 2.0
_COMMAND_MAX_SECONDS = 30.0


class WakeWordListener:
    """Слушает в нативном аудиопотоке, чтобы распознавание работало при свёрнутом UI."""

    def __init__(self, on_update: Callable[[dict], None] | None = None) -> None:
        self._on_update = on_update
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._finish_command = threading.Event()
        self._cancel_command = threading.Event()
        self._audio_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._frames: deque[tuple[float, object, float]] = deque(maxlen=180)
        self._sample_rate = 16_000
        self._state = {
            "enabled": False,
            "listening": False,
            "awaiting_command": False,
            "needs_followup": False,
            "recognizing": False,
            "phase": "disabled",
            "last_transcript": None,
            "last_response": None,
            "speech_queued": False,
            "active_request_id": None,
            "error": None,
        }

    def status(self) -> dict:
        with self._lock:
            return {**self._state, "thread_running": bool(self._thread and self._thread.is_alive())}

    def _publish(self, **changes) -> None:
        with self._lock:
            self._state.update(changes)
            snapshot = dict(self._state)
        if self._on_update:
            try:
                self._on_update(snapshot)
            except Exception:
                logger.debug("Не удалось обновить состояние фонового голосового режима", exc_info=True)

    def start(self) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._publish(enabled=True)
                return self.status()
            self._stop.clear()
            self._finish_command.clear()
            self._cancel_command.clear()
            self._audio_ready.clear()
            self._publish(enabled=True, listening=False, awaiting_command=False, phase="starting", error=None)
            self._thread = threading.Thread(target=self._run, name="neo-wake-word", daemon=True)
            self._thread.start()
        return self.status()

    def stop(self, wait: bool = True) -> dict:
        self._publish(enabled=False, listening=False, awaiting_command=False, recognizing=False, phase="disabled")
        self._stop.set()
        self._finish_command.set()
        self._cancel_command.set()
        self._audio_ready.set()
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.debug("Не удалось сразу остановить аудиопоток", exc_info=True)
        thread = self._thread
        if wait and thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=6)
        return self.status()

    def finish_command(self) -> dict:
        """Просит активную запись после фразы активации немедленно завершиться."""
        state = self.status()
        if not state["enabled"] or not state["awaiting_command"]:
            return {"requested": False, **state}
        self._finish_command.set()
        self._audio_ready.set()
        return {"requested": True, **self.status()}

    def cancel_command(self) -> dict:
        """Отбрасывает фразу, записанную после фразы активации, не отправляя её."""
        state = self.status()
        cancellable = state["awaiting_command"] or state["phase"] == "transcribing"
        if not state["enabled"] or not cancellable:
            return {"requested": False, **state}
        self._cancel_command.set()
        self._audio_ready.set()
        return {"requested": True, **self.status()}

    def _append_audio(self, indata, _frames, _time, status) -> None:
        import numpy as np

        if status:
            logger.debug("Состояние аудиопотока wake-word: %s", status)
        samples = np.asarray(indata[:, 0], dtype=np.float32).copy()
        rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
        with self._lock:
            self._frames.append((time.monotonic(), samples, rms))
        self._audio_ready.set()

    def _snapshot(self, after: float = 0.0) -> list[tuple[float, object, float]]:
        with self._lock:
            return [frame for frame in self._frames if frame[0] >= after]

    @staticmethod
    def _to_16khz(frames: list[tuple[float, object, float]], sample_rate: int):
        import numpy as np

        if not frames:
            return np.empty(0, dtype=np.float32)
        samples = np.concatenate([frame[1] for frame in frames])
        if sample_rate == 16_000:
            return samples
        target_length = max(1, round(len(samples) * 16_000 / sample_rate))
        old_positions = np.arange(len(samples), dtype=np.float64)
        new_positions = np.linspace(0, len(samples) - 1, target_length)
        return np.interp(new_positions, old_positions, samples).astype(np.float32)

    @staticmethod
    def _ready_chime() -> None:
        import numpy as np
        import sounddevice as sd

        rate = 24_000
        tone_time = np.arange(int(rate * 0.16), dtype=np.float32) / rate
        first = np.sin(2 * np.pi * 660 * tone_time) * np.linspace(0.18, 0, len(tone_time))
        second = np.sin(2 * np.pi * 880 * tone_time) * np.linspace(0.18, 0, len(tone_time))
        gap = np.zeros(int(rate * 0.045), dtype=np.float32)
        sd.play(np.concatenate((first, gap, second)), samplerate=rate,
                device=audio_devices.selected_device("output"))
        sd.wait()

    def _recognize(self, frames: list[tuple[float, object, float]], sample_rate: int) -> str:
        from .stt import transcribe_samples

        samples = self._to_16khz(frames, sample_rate)
        return transcribe_samples(samples)

    def _send_command(self, text: str) -> bool:
        command = text.strip()
        if not command:
            self._publish(awaiting_command=False, listening=True, phase="listening_wake", last_response="Не удалось разобрать команду. Скажите «Привет Нео» ещё раз.", speech_queued=False)
            return False
        from .stt import normalize_transcript
        command = normalize_transcript(command)
        normalized_command = re.sub(r"[^a-zа-яё]+", " ", command.casefold()).strip()
        stop_phrases = {
            "нео стоп", "стоп нео", "neo stop", "stop neo",
            "нео хватит", "хватит нео", "нео отмена", "отмена нео",
            "нео отмени", "нео остановись", "нео прекрати",
            "отмена диалога", "остановить диалог",
        }
        if normalized_command in stop_phrases:
            from neo.ai import context_manager
            from .tts import stop_speech

            context_manager.clear_context("wake-word")
            context_manager.clear_pending_conflict("wake-word")
            stop_speech()
            self._publish(
                recognizing=False, awaiting_command=False, listening=True,
                phase="responded", last_transcript=command,
                last_response="Диалог остановлен.", speech_queued=False,
                needs_followup=False, error=None,
            )
            return False
        self._publish(
            recognizing=True, awaiting_command=False, phase="thinking",
            last_transcript=command, last_response=None, speech_queued=False,
            needs_followup=False, error=None,
        )
        request_id = uuid.uuid4().hex
        payload = json.dumps({
            "text": command,
            "session_id": "wake-word",
            "request_id": request_id,
            "speak": True,
        }).encode("utf-8")
        self._publish(active_request_id=request_id)
        request = urllib.request.Request(
            "http://127.0.0.1:8000/api/text", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.loads(response.read().decode("utf-8"))
            speech_queued = bool(result.get("speech_queued"))
            needs_followup = result.get("action") in {"clarify", "conflict"}
            self._publish(
                recognizing=False,
                listening=True,
                phase=(
                    "cancelled" if result.get("action") == "cancel"
                    else "speaking" if speech_queued
                    else "responded"
                ),
                last_response=result.get("response_text", "Команда обработана."),
                speech_queued=speech_queued,
                needs_followup=needs_followup,
                active_request_id=None,
                error=None,
            )
            return needs_followup
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                message = payload.get("detail") or f"Ошибка сервера: {exc.code}"
            except (ValueError, AttributeError):
                message = f"Ошибка сервера: {exc.code}"
            logger.warning("Фоновая команда отклонена сервером (%s): %s", exc.code, message)
            self._publish(
                recognizing=False, listening=True, last_response=message,
                speech_queued=False, needs_followup=False, active_request_id=None,
                phase="error", error=message,
            )
            return False
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            timed_out = isinstance(exc, TimeoutError) or (
                isinstance(exc, urllib.error.URLError)
                and isinstance(exc.reason, TimeoutError)
            )
            message = f"Не удалось обработать команду: {exc}"
            logger.warning("Фоновая команда не отправлена: %s", message)
            self._publish(
                recognizing=False, listening=True, last_response=message,
                speech_queued=False, needs_followup=False,
                active_request_id=request_id if timed_out else None,
                phase="error", error=message,
            )
            return False

    def publish_api_response(self, command: str, response: dict) -> None:
        """Обновляет десктопный UI, если API завершает обработку после таймаута HTTP-клиента."""
        state = self.status()
        if not state["enabled"] or state.get("last_transcript") != command:
            return
        action = response.get("action")
        self._publish(
            recognizing=False,
            awaiting_command=False,
            listening=True,
            phase=(
                "cancelled" if action == "cancel"
                else "speaking" if response.get("speech_queued")
                else "responded"
            ),
            last_response=response.get("response_text", "Команда обработана."),
            speech_queued=bool(response.get("speech_queued")),
            needs_followup=action in {"clarify", "conflict"},
            active_request_id=None,
            error=None,
        )

    def _run(self) -> None:
        stream = None
        try:
            import sounddevice as sd
            import numpy as np

            from .wake_detector import create_detector, phrase_detected

            audio_devices.list_devices()
            device = audio_devices.selected_device("input")
            info = sd.query_devices(device, "input") if device is not None else sd.query_devices(kind="input")
            preferred_rate = 16_000
            try:
                stream = sd.InputStream(
                    device=device, samplerate=preferred_rate, channels=1, dtype="float32",
                    blocksize=3_200, callback=self._append_audio,
                )
                self._sample_rate = preferred_rate
            except Exception:
                self._sample_rate = int(info["default_samplerate"])
                stream = sd.InputStream(
                    device=device, samplerate=self._sample_rate, channels=1, dtype="float32",
                    blocksize=max(1, self._sample_rate // 5), callback=self._append_audio,
                )
            with self._lock:
                self._frames.clear()
            self._publish(phase="loading_wake_model")
            _model, wake_recognizer = create_detector(
                self._sample_rate,
                on_status=lambda: self._publish(phase="downloading_wake_model"),
            )
            stream.start()
            with self._lock:
                self._stream = stream
            self._publish(listening=True, phase="listening_wake", error=None)
            logger.info("Фоновое распознавание активно на устройстве: %s", info["name"])
            last_wake = 0.0
            wake_cursor = time.monotonic()
            command_started = 0.0
            command_heard = False
            silence_since = None

            while not self._stop.is_set():
                self._audio_ready.wait(timeout=0.3)
                self._audio_ready.clear()
                now = time.monotonic()

                if command_started:
                    if self._cancel_command.is_set():
                        self._cancel_command.clear()
                        self._finish_command.clear()
                        command_started = 0.0
                        command_heard = False
                        silence_since = None
                        wake_recognizer.Reset()
                        wake_cursor = now
                        self._publish(
                            awaiting_command=False, recognizing=False,
                            listening=True, phase="listening_wake",
                            last_response="Запись отменена. Команда не отправлена.",
                            active_request_id=None,
                        )
                        continue
                    finish_requested = self._finish_command.is_set()
                    if finish_requested:
                        self._finish_command.clear()
                    command_frames = self._snapshot(command_started)
                    if finish_requested and not command_frames:
                        command_started = 0.0
                        command_heard = False
                        silence_since = None
                        wake_recognizer.Reset()
                        wake_cursor = now
                        self._publish(
                            awaiting_command=False, recognizing=False,
                            listening=True, phase="listening_wake",
                        )
                        continue
                    if command_frames:
                        if command_frames[-1][2] > 0.006:
                            command_heard = True
                            silence_since = None
                        elif command_heard and silence_since is None:
                            silence_since = now
                        if finish_requested and max(frame[2] for frame in command_frames) > 0.003:
                            command_heard = True
                        duration = now - command_started
                        if command_heard and (
                            finish_requested
                            or now - (silence_since or now) >= _COMMAND_END_SILENCE_SECONDS
                            or duration >= _COMMAND_MAX_SECONDS
                        ):
                            self._publish(recognizing=True, awaiting_command=False, phase="transcribing")
                            command = self._recognize(command_frames, self._sample_rate)
                            if self._cancel_command.is_set():
                                self._cancel_command.clear()
                                command_started = 0.0
                                command_heard = False
                                silence_since = None
                                wake_recognizer.Reset()
                                wake_cursor = time.monotonic()
                                self._publish(
                                    awaiting_command=False, recognizing=False,
                                    listening=True, phase="listening_wake",
                                    last_response="Запись отменена. Команда не отправлена.",
                                    active_request_id=None,
                                )
                                continue
                            if self._stop.is_set():
                                break
                            needs_followup = self._send_command(command)
                            from .tts import get_status as get_speech_status
                            speech_deadline = time.monotonic() + 120
                            while not self._stop.is_set() and time.monotonic() < speech_deadline:
                                playback = get_speech_status()
                                if not playback["speaking"] and not playback["queued_items"]:
                                    break
                                self._stop.wait(0.2)
                            if self._stop.is_set():
                                break
                            if needs_followup:
                                try:
                                    self._ready_chime()
                                except Exception:
                                    logger.exception("Не удалось подать сигнал перед уточнением")
                                command_started = time.monotonic()
                                command_heard = False
                                silence_since = None
                                wake_recognizer.Reset()
                                wake_cursor = command_started
                                self._publish(
                                    phase="listening_followup", listening=True,
                                    awaiting_command=True, recognizing=False,
                                )
                                continue
                            if not self.status()["error"]:
                                self._publish(phase="listening_wake", listening=True, recognizing=False)
                            command_started = 0.0
                            command_heard = False
                            silence_since = None
                            wake_recognizer.Reset()
                            wake_cursor = time.monotonic()
                        elif finish_requested:
                            command_started = 0.0
                            command_heard = False
                            silence_since = None
                            wake_recognizer.Reset()
                            wake_cursor = now
                            self._publish(
                                awaiting_command=False, recognizing=False,
                                listening=True, phase="listening_wake",
                            )
                    if command_started and now - command_started >= _COMMAND_MAX_SECONDS:
                        self._publish(awaiting_command=False, listening=True, phase="listening_wake", last_response="Время ожидания команды истекло.", speech_queued=False, needs_followup=False)
                        command_started = 0.0
                    continue

                if now - last_wake < 3.5:
                    continue
                wake_frames = self._snapshot(wake_cursor)
                if not wake_frames:
                    continue
                wake_cursor = wake_frames[-1][0] + 0.000001
                from .tts import get_status as get_speech_status
                playback = get_speech_status()
                if playback["speaking"] or playback["queued_items"]:
                    wake_recognizer.Reset()
                    continue
                samples = np.concatenate([frame[1] for frame in wake_frames])
                pcm = np.clip(samples * 32767.0, -32768, 32767).astype("<i2").tobytes()
                try:
                    wake_recognizer.AcceptWaveform(pcm)
                    partial = json.loads(wake_recognizer.PartialResult()).get("partial", "")
                except (ValueError, RuntimeError):
                    logger.debug("Не удалось обработать аудиоблок детектором Vosk", exc_info=True)
                    continue
                if not phrase_detected(partial):
                    continue

                last_wake = time.monotonic()
                self._publish(last_transcript=partial, last_response=None, speech_queued=False, needs_followup=False, awaiting_command=True, phase="wake_detected", error=None)
                try:
                    self._ready_chime()
                except Exception:
                    logger.exception("Не удалось воспроизвести сигнал готовности Neo")
                command_started = time.monotonic()
                wake_recognizer.Reset()
                wake_cursor = command_started
                self._publish(awaiting_command=True, recognizing=False, phase="listening_command")

        except Exception as exc:
            logger.exception("Фоновое распознавание Neo завершилось с ошибкой")
            self._publish(listening=False, awaiting_command=False, recognizing=False, phase="error", error=str(exc))
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    logger.debug("Ошибка при закрытии фонового аудиопотока", exc_info=True)
            with self._lock:
                restart = self._stop.is_set() and self._state["enabled"]
                if restart:
                    self._thread = None
            self._publish(listening=False, awaiting_command=False, recognizing=False, phase="disabled" if self._stop.is_set() else "error")
            if restart and self.status()["enabled"]:
                self.start()