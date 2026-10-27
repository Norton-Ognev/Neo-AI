"""Внутрипроцессные сигналы отмены для активных запросов к ассистенту."""

from __future__ import annotations

from threading import Event, Lock


class RequestCancelled(Exception):
    """Возникает, когда пользователь отменяет запрос к ассистенту."""


_lock = Lock()
_requests: dict[str, Event] = {}


def register_request(request_id: str | None) -> Event:
    if not request_id:
        return Event()
    event = Event()
    with _lock:
        _requests[request_id] = event
    return event


def cancel_request(request_id: str) -> bool:
    with _lock:
        event = _requests.get(request_id)
    if event is None:
        return False
    event.set()
    return True


def unregister_request(request_id: str | None, event: Event) -> None:
    if not request_id:
        return
    with _lock:
        if _requests.get(request_id) is event:
            _requests.pop(request_id, None)


def raise_if_cancelled(event: Event | None) -> None:
    if event is not None and event.is_set():
        raise RequestCancelled