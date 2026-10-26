"""Manage the Neo shortcut in the current user's Windows Startup folder."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _shortcut_path() -> Path:
    app_data = os.environ.get("APPDATA")
    if not app_data:
        raise RuntimeError("Не удалось найти папку автозапуска Windows для текущего пользователя.")
    return Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Neo.lnk"


def is_enabled() -> bool:
    return _shortcut_path().is_file()


def set_enabled(enabled: bool) -> bool:
    shortcut_path = _shortcut_path()
    if not enabled:
        shortcut_path.unlink(missing_ok=True)
        return False

    if sys.platform != "win32":
        raise RuntimeError("Автозапуск через папку Startup доступен только в Windows.")
    try:
        from win32com.client import Dispatch
    except ImportError as exc:
        raise RuntimeError("Для настройки автозапуска установите зависимость pywin32.") from exc

    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    shortcut = Dispatch("WScript.Shell").CreateShortcut(str(shortcut_path))
    shortcut.TargetPath = sys.executable
    shortcut.WorkingDirectory = str(Path(__file__).resolve().parents[2])
    shortcut.Description = "Запуск голосового помощника Neo"
    shortcut.IconLocation = f"{sys.executable},0"
    shortcut.Arguments = "" if getattr(sys, "frozen", False) else "-m neo.desktop.launcher"
    shortcut.save()
    return True
