from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from ..db.database import get_connection
from ..schemas.event import EventCreate, EventUpdate


def _serialize_event(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "event_type": row["event_type"],
        "content": row["content"],
        "datetime_iso": row["datetime_iso"],
        "created_at": row["created_at"],
        "is_completed": bool(row["is_completed"]),
    }


class EventService:
    """Доступ к записям событий на уровне приложения."""

    @staticmethod
    def create(event: EventCreate) -> dict:
        created_at = datetime.now(timezone.utc).isoformat()
        datetime_iso = event.datetime_iso.isoformat() if event.datetime_iso else None
        with get_connection() as connection:
            cursor = connection.execute(
                """INSERT INTO events (event_type, content, datetime_iso, created_at)
                   VALUES (?, ?, ?, ?)""",
                (event.event_type, event.content.strip(), datetime_iso, created_at),
            )
            row = connection.execute(
                "SELECT * FROM events WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return _serialize_event(row)

    @staticmethod
    def update(event_id: int, event: EventUpdate) -> dict | None:
        datetime_iso = event.datetime_iso.isoformat() if event.datetime_iso else None
        with get_connection() as connection:
            cursor = connection.execute(
                """UPDATE events
                   SET event_type = ?, content = ?, datetime_iso = ?
                   WHERE id = ?""",
                (event.event_type, event.content.strip(), datetime_iso, event_id),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()
            return _serialize_event(row)

    @staticmethod
    def delete(event_id: int) -> bool:
        with get_connection() as connection:
            cursor = connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
            return cursor.rowcount > 0

    @staticmethod
    def find_conflicts(datetime_iso: datetime, tolerance_seconds: int = 30) -> list[dict]:
        """Находит незавершённые события, запланированные в пределах 30 секунд от указанного времени."""
        with get_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM events
                   WHERE is_completed = 0 AND datetime_iso IS NOT NULL
                     AND ABS(julianday(datetime_iso) - julianday(?)) * 86400 <= ?
                   ORDER BY id""",
                (datetime_iso.isoformat(), tolerance_seconds),
            ).fetchall()
            return [_serialize_event(row) for row in rows]

    @staticmethod
    def list_events(
        start: datetime | None = None,
        end: datetime | None = None,
        include_completed: bool = True,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[str] = []
        if start is not None:
            clauses.append("julianday(datetime_iso) >= julianday(?)")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("julianday(datetime_iso) < julianday(?)")
            params.append(end.isoformat())
        if not include_completed:
            clauses.append("is_completed = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with get_connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM events {where} "
                "ORDER BY datetime_iso IS NULL, julianday(datetime_iso), id",
                params,
            ).fetchall()
            return [_serialize_event(row) for row in rows]

    @staticmethod
    def search(
        query: str,
        limit: int | None = 50,
        start: datetime | None = None,
    ) -> list[dict]:
        terms = [term for term in query.strip().split() if term]
        if not terms:
            return []
        # Требуем присутствия каждого слова, но разрешаем им идти в любом порядке.
        where = " AND ".join(
            "neo_casefold(content) LIKE neo_casefold(?) ESCAPE '\\'" for _ in terms
        )
        escaped_terms = [
            term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            for term in terms
        ]
        params = [f"%{term}%" for term in escaped_terms]
        if start is not None:
            where += " AND datetime_iso IS NOT NULL AND julianday(datetime_iso) >= julianday(?)"
            params.append(start.isoformat())
        if limit is not None:
            params.append(limit)
        with get_connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM events WHERE {where} "
                "ORDER BY julianday(datetime_iso) DESC, id DESC"
                + (" LIMIT ?" if limit is not None else ""),
                params,
            ).fetchall()
            return [_serialize_event(row) for row in rows]