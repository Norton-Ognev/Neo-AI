"""Native microphone bridge for pywebview; avoids browser microphone prompts."""

from __future__ import annotations

import base64
import io
import json
import wave
from threading import RLock

from neo.voice import audio_devices
from neo.voice.wake_word import WakeWordListener
from . import autostart

_active_bridge: AudioBridge | None = None


def publish_wake_response(command: str, response: dict) -> None:
    bridge = _active_bridge
    if bridge is not None:
        bridge._wake_listener.publish_api_response(command, response)


class AudioBridge:
    def __init__(self) -> None:
        global _active_bridge
        self._lock = RLock()
        self._stream = None
        self._chunks = []
        self._sample_rate = 16_000
        self._error: str | None = None
        self._wake_enabled = audio_devices.get_wake_word_enabled()
        self._wake_listener = WakeWordListener(self._notify_wake_update)
        self._window = None
        self._closing = False
        _active_bridge = self

    def bind_window(self, window) -> None:
        self._window = window

    def _notify_wake_update(self, state: dict) -> None:
        if self._closing or not self._window:
            return
        try:
            payload = json.dumps(state, ensure_ascii=False)
            self._window.evaluate_js(f"window.dispatchEvent(new CustomEvent('neo-wake-state', {{detail: {payload}}}))")
        except Exception:
            pass

    def get_audio_devices(self) -> dict:
        return audio_devices.list_devices()

    def set_audio_device(self, kind: str, device_id: str) -> dict:
        restart_listener = self._wake_enabled and self._wake_listener.status()["listening"]
        if restart_listener:
            self._wake_listener.stop()
        settings = audio_devices.set_device(kind, device_id)
        if restart_listener:
            self._wake_listener.start()
        return settings

    def get_wake_enabled(self) -> bool:
        return self._wake_enabled

    def get_wake_status(self) -> dict:
        return self._wake_listener.status()

    def finish_wake_command(self) -> dict:
        return self._wake_listener.finish_command()

    def cancel_wake_command(self) -> dict:
        return self._wake_listener.cancel_command()

    def set_wake_enabled(self, enabled: bool) -> dict:
        self._wake_enabled = audio_devices.set_wake_word_enabled(enabled)
        if self._wake_enabled:
            return self._wake_listener.start()
        return self._wake_listener.stop()

    def resume_wake_listener(self) -> dict:
        if self._wake_enabled:
            return self._wake_listener.start()
        return self._wake_listener.status()

    def set_output_volume(self, value: float) -> float:
        return audio_devices.set_volume(value)

    def get_autostart(self) -> bool:
        return autostart.is_enabled()

    def set_autostart(self, enabled: bool) -> bool:
        return autostart.set_enabled(bool(enabled))

    def start_recording(self) -> dict:
        import sounddevice as sd

        if self._wake_enabled:
            self._wake_listener.stop()
        try:
            with self._lock:
                if self._stream is not None:
                    return {"recording": True}
                audio_devices.list_devices()
                selected = audio_devices.selected_device("input")
                info = sd.query_devices(selected, "input") if selected is not None else sd.query_devices(kind="input")
                self._sample_rate = int(info["default_samplerate"])
                self._chunks = []
                self._error = None

                def collect(indata, _frames, _time, status):
                    if status:
                        self._error = str(status)
                    self._chunks.append(indata.copy())

                self._stream = sd.InputStream(
                    device=selected,
                    samplerate=self._sample_rate,
                    channels=1,
                    dtype="int16",
                    callback=collect,
                )
                self._stream.start()
        except Exception as exc:
            with self._lock:
                self._stream = None
            if self._wake_enabled:
                self._wake_listener.start()
            raise RuntimeError(f"Не удалось открыть микрофон: {exc}") from exc
        return {"recording": True, "device": info["name"], "sample_rate": self._sample_rate}

    def stop_recording(self) -> dict:
        import numpy as np
        with self._lock:
            stream = self._stream
            if stream is None:
                raise RuntimeError("Запись не запущена.")
            self._stream = None
            try:
                stream.stop()
            finally:
                stream.close()
            chunks = self._chunks
            self._chunks = []
            sample_rate = self._sample_rate
            error = self._error
        if not chunks:
            raise RuntimeError(error or "Микрофон не передал аудиоданные.")

        pcm = np.concatenate(chunks, axis=0).reshape(-1).astype("<i2", copy=False).tobytes()
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm)
        return {"audio_base64": base64.b64encode(output.getvalue()).decode("ascii"), "recording": False}

    def cancel_recording(self) -> dict:
        with self._lock:
            stream = self._stream
            self._stream = None
            self._chunks = []
            self._error = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        with self._lock:
            self._chunks = []
        if self._wake_enabled:
            self._wake_listener.start()
        return {"cancelled": True}

    def shutdown(self) -> None:
        global _active_bridge
        self._closing = True
        if _active_bridge is self:
            _active_bridge = None
        self._window = None
        self._wake_enabled = False
        self._wake_listener.stop(wait=False)
        with self._lock:
            stream = self._stream
            self._stream = None
            self._chunks = []
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
