from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Response

from ..schemas.event import EventCreate, EventRead, EventUpdate
from ..services.event_service import EventService

router = APIRouter(prefix="/api/events", tags=["events"])


@router.post("", response_model=EventRead, status_code=201)
def create_event(event: EventCreate) -> dict:
    try:
        return EventService.create(event)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{event_id}", response_model=EventRead)
def update_event(event_id: int, event: EventUpdate) -> dict:
    try:
        updated = EventService.update(event_id, event)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return updated


@router.delete("/{event_id}", status_code=204)
def delete_event(event_id: int) -> Response:
    if not EventService.delete(event_id):
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return Response(status_code=204)


@router.get("", response_model=list[EventRead])
def list_events(
    start: datetime | None = None,
    end: datetime | None = None,
    include_completed: bool = True,
) -> list[dict]:
    if start and end and start >= end:
        raise HTTPException(status_code=422, detail="start должен быть раньше end")
    return EventService.list_events(start, end, include_completed)


@router.get("/search", response_model=list[EventRead])
def search_events(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    return EventService.search(q, limit)
