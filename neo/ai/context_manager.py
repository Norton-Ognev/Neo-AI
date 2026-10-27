# ai/context_manager.py
from datetime import datetime, timedelta
from .schemas import Intent

# Локальное хранилище сессий в памяти (Key: session_id, Value: dict)
_session_storage = {}
_pending_conflicts = {}


def get_pending_conflict(session_id: str = "default") -> dict | None:
    pending = _pending_conflicts.get(session_id)
    if not pending:
        return None
    if datetime.now() - pending["updated_at"] > timedelta(minutes=5):
        _pending_conflicts.pop(session_id, None)
        return None
    return pending


def save_pending_conflict(
    session_id: str, intent: Intent, conflicts: list[dict],
    awaiting_time: bool = False, event_type: str = "task",
):
    _pending_conflicts[session_id] = {
        "intent": intent,
        "conflicts": conflicts,
        "awaiting_time": awaiting_time,
        "event_type": event_type,
        "updated_at": datetime.now(),
    }


def clear_pending_conflict(session_id: str = "default"):
    _pending_conflicts.pop(session_id, None)


def get_last_intent(session_id: str = "default") -> Intent | None:
    """Получает последний сохраненный интент, если он не устарел (живет 5 минут)."""
    if session_id not in _session_storage:
        return None

    session_data = _session_storage[session_id]
    # Проверяем "свежесть" контекста
    if datetime.now() - session_data["updated_at"] > timedelta(minutes=5):
        del _session_storage[session_id]
        return None

    return session_data["intent"]


def get_context_flags(session_id: str = "default") -> dict:
    """Возвращает состояние «ожидает уточнения» для текущего диалога."""
    session_data = _session_storage.get(session_id)
    if not session_data:
        return {
            "awaiting_meridiem": False,
            "awaiting_date": False,
            "awaiting_time": False,
        }
    return {
        "awaiting_meridiem": session_data.get("awaiting_meridiem", False),
        "awaiting_date": session_data.get("awaiting_date", False),
        "awaiting_time": session_data.get("awaiting_time", False),
    }


def save_intent(
    intent: Intent,
    session_id: str = "default",
    *,
    awaiting_meridiem: bool = False,
    awaiting_date: bool = False,
    awaiting_time: bool = False,
):
    """Сохраняет интент в память для последующего склеивания контекста."""
    _session_storage[session_id] = {
        "intent": intent,
        "updated_at": datetime.now(),
        "awaiting_meridiem": awaiting_meridiem,
        "awaiting_date": awaiting_date,
        "awaiting_time": awaiting_time,
    }


def clear_context(session_id: str = "default"):
    """Очищает память сессии после успешного создания задачи/встречи."""
    if session_id in _session_storage:
        del _session_storage[session_id]
