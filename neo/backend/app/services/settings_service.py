"""Persistent app settings shared with the desktop audio settings.

Файл `settings.json` в LOCALAPPDATA/Neo уже используется модулем
`neo.voice.audio_devices` (устройства ввода/вывода, громкость, wake-word).
Этот сервис читает/пишет тот же файл, но работает со своими ключами
(например, `reminder_days_ahead`), аккуратно сохраняя чужие значения.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

_lock = RLock()

DEFAULT_SETTINGS: dict[str, Any] = {
    "reminder_days_ahead": 3,
}

_MIN_REMINDER_DAYS = 1
_MAX_REMINDER_DAYS = 365

def _settings_path() -> Path:
    root = os.getenv("LOCALAPPDATA")
    base = Path(root) / "Neo" if root else Path.home() / ".config" / "Neo"
    return base / "settings.json"

def _read_raw() -> dict:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}

def _write_raw(data: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)

def _normalize_reminder_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = DEFAULT_SETTINGS["reminder_days_ahead"]
    return max(_MIN_REMINDER_DAYS, min(days, _MAX_REMINDER_DAYS))

def get_settings() -> dict:
    """Возвращает эффективные настройки с подставленными значениями по умолчанию."""
    with _lock:
        stored = _read_raw()
    merged = {**DEFAULT_SETTINGS, **stored}
    merged["reminder_days_ahead"] = _normalize_reminder_days(
        merged.get("reminder_days_ahead")
    )
    return merged

def update_settings(patch: dict) -> dict:
    """Сливает патч с текущим содержимым settings.json и возвращает итоговый набор."""
    with _lock:
        stored = _read_raw()
        stored.update(patch)
        if "reminder_days_ahead" in stored:
            stored["reminder_days_ahead"] = _normalize_reminder_days(
                stored["reminder_days_ahead"]
            )
        _write_raw(stored)
    return get_settings()
