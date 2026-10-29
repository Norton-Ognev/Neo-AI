from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from ..db.database import get_connection
from ..schemas.event import EventCreate, EventUpdate

_SEARCH_STOPWORDS = {
    "с", "со", "в", "во", "о", "об", "обо", "к", "ко", "у", "на", "по", "за",
    "из", "от", "до", "и", "а", "про", "для", "над", "под", "при",
    "меня", "мне", "есть", "будет", "ли", "бы",
}


def _search_stem(term: str) -> str:
    """Грубая «основа» слова, чтобы «встречу с Димой» находила «встреча с Димой»,
    а «прогулку» — «прогулка». Предлоги выкидываем: они только мешают."""
    word = term.strip(" ,.!?:;«»\"'").casefold()
    if word in _SEARCH_STOPWORDS:
        return ""
    if any(ch.isdigit() for ch in word):
        return word
    if len(word) >= 5:
        return word[:-2]
    if len(word) == 4:
        return word[:-1]
    return word


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
    def list_upcoming_by_type(
        event_type: str,
        within_days: int,
        include_completed: bool = False,
    ) -> list[dict]:
        """Возвращает события заданного типа за ближайшие N дней по возрастанию времени.

        Используется для блока «Напоминания» на главном экране: показываем только то,
        что попадёт в окно `[now, now + N дней]`, а не весь календарь.
        """
        now = datetime.now().astimezone()
        end = now + timedelta(days=max(1, int(within_days)))
        clauses = [
            "event_type = ?",
            "datetime_iso IS NOT NULL",
            "julianday(datetime_iso) >= julianday(?)",
            "julianday(datetime_iso) < julianday(?)",
        ]
        params: list = [event_type, now.isoformat(), end.isoformat()]
        if not include_completed:
            clauses.append("is_completed = 0")
        with get_connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM events WHERE {' AND '.join(clauses)} "
                "ORDER BY julianday(datetime_iso), id",
                params,
            ).fetchall()
            return [_serialize_event(row) for row in rows]

    @staticmethod
    def list_by_type(event_type: str, include_completed: bool = True) -> list[dict]:
        """Возвращает все события заданного типа.

        Активные (не выполненные) идут раньше завершённых, внутри — по времени дедлайна.
        События без даты идут после событий с датой, но перед завершёнными.
        """
        clauses = ["event_type = ?"]
        params: list = [event_type]
        if not include_completed:
            clauses.append("is_completed = 0")
        with get_connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM events WHERE {' AND '.join(clauses)} "
                "ORDER BY is_completed, datetime_iso IS NULL, "
                "julianday(datetime_iso), id",
                params,
            ).fetchall()
            return [_serialize_event(row) for row in rows]

    @staticmethod
    def search(
        query: str,
        limit: int | None = 50,
        start: datetime | None = None,
    ) -> list[dict]:
        terms = [_search_stem(term) for term in query.strip().split()]
        terms = [term for term in terms if term]
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
            if rows or len(terms) < 2:
                return [_serialize_event(row) for row in rows]

            # Запасной вариант: «все слова сразу» ничего не нашло (лишнее слово в запросе,
            # ошибка распознавания речи). Берём записи, где совпала хотя бы половина слов,
            # и показываем сначала те, где совпадений больше.
            needed = max(1, (len(terms) + 1) // 2)
            like_params = [f"%{term}%" for term in escaped_terms]
            any_where = " OR ".join(
                "neo_casefold(content) LIKE neo_casefold(?) ESCAPE '\\'" for _ in terms
            )
            fallback_params = list(like_params)
            date_filter = ""
            if start is not None:
                date_filter = (
                    " AND datetime_iso IS NOT NULL"
                    " AND julianday(datetime_iso) >= julianday(?)"
                )
                fallback_params.append(start.isoformat())
            candidates = connection.execute(
                f"SELECT * FROM events WHERE ({any_where}){date_filter} "
                "ORDER BY julianday(datetime_iso) DESC, id DESC",
                fallback_params,
            ).fetchall()
            scored = []
            for row in candidates:
                content = (row["content"] or "").casefold()
                hits = sum(1 for term in terms if term.casefold() in content)
                if hits >= needed:
                    scored.append((hits, row))
            scored.sort(key=lambda item: -item[0])  # сортировка устойчива: дата остаётся убывающей
            ranked = [row for _, row in scored]
            if limit is not None:
                ranked = ranked[:limit]
            return [_serialize_event(row) for row in ranked]