"""Windows audio device selection shared by the desktop UI and voice services."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock


_lock = RLock()


class AudioDeviceError(RuntimeError):
    pass


def _settings_path() -> Path:
    root = os.getenv("LOCALAPPDATA")
    base = Path(root) / "Neo" if root else Path.home() / ".config" / "Neo"
    return base / "settings.json"


def _load() -> dict:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(settings: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _sounddevice():
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise AudioDeviceError("Не установлен пакет sounddevice. Переустановите зависимости Neo.") from exc
    return sd


def _default_indices(sd) -> tuple[int | None, int | None]:
    values = sd.default.device
    return tuple(int(value) if value is not None and int(value) >= 0 else None for value in values)


def list_devices() -> dict:
    sd = _sounddevice()
    try:
        devices = list(sd.query_devices())
        host_apis = list(sd.query_hostapis())
        default_input, default_output = _default_indices(sd)
    except Exception as exc:
        raise AudioDeviceError(f"Не удалось получить список аудиоустройств Windows: {exc}") from exc

    inputs, outputs = [], []
    for device in devices:
        host_api_index = int(device["hostapi"])
        host_api = host_apis[host_api_index]["name"] if host_api_index < len(host_apis) else ""
        item = {
            "id": str(device["index"]),
            "name": str(device["name"]),
            "host_api": host_api,
            "system_default": False,
        }
        if int(device["max_input_channels"]) > 0:
            inputs.append({**item, "system_default": int(device["index"]) == default_input})
        if int(device["max_output_channels"]) > 0:
            outputs.append({**item, "system_default": int(device["index"]) == default_output})

    settings = _load()
    input_ids = {device["id"] for device in inputs}
    output_ids = {device["id"] for device in outputs}
    stale_selection = False
    if settings.get("input_device") not in input_ids and settings.get("input_device") is not None:
        settings["input_device"] = None
        stale_selection = True
    if settings.get("output_device") not in output_ids and settings.get("output_device") is not None:
        settings["output_device"] = None
        stale_selection = True
    if stale_selection:
        _save(settings)
    return {
        "inputs": inputs,
        "outputs": outputs,
        "selected_input": settings.get("input_device"),
        "selected_output": settings.get("output_device"),
        "default_input": str(default_input) if default_input is not None else None,
        "default_output": str(default_output) if default_output is not None else None,
        "volume": float(settings.get("volume", 1.0)),
    }


def set_device(kind: str, device_id: str | None) -> dict:
    if kind not in {"input", "output"}:
        raise AudioDeviceError("Тип устройства должен быть input или output.")
    sd = _sounddevice()
    setting_key = f"{kind}_device"
    normalized = None if device_id in (None, "", "system-default") else str(device_id)
    if normalized is not None:
        try:
            index = int(normalized)
            device = sd.query_devices(index)
            channels = device["max_input_channels" if kind == "input" else "max_output_channels"]
            if int(channels) <= 0:
                raise ValueError("Устройство не поддерживает нужный тип аудио.")
        except Exception as exc:
            raise AudioDeviceError("Выбранное устройство недоступно.") from exc
    with _lock:
        settings = _load()
        settings[setting_key] = normalized
        _save(settings)
    return list_devices()


def selected_device(kind: str) -> int | None:
    value = _load().get(f"{kind}_device")
    if value in (None, "", "system-default"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def set_volume(value: float) -> float:
    volume = max(0.0, min(1.0, float(value)))
    with _lock:
        settings = _load()
        settings["volume"] = volume
        _save(settings)
    return volume


def get_volume() -> float:
    try:
        return max(0.0, min(1.0, float(_load().get("volume", 1.0))))
    except (TypeError, ValueError):
        return 1.0


def get_wake_word_enabled() -> bool:
    return bool(_load().get("wake_word_enabled", True))


def set_wake_word_enabled(enabled: bool) -> bool:
    with _lock:
        settings = _load()
        settings["wake_word_enabled"] = bool(enabled)
        _save(settings)
    return bool(enabled)
