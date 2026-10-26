"""SQLite connection and schema management for Neo."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def get_database_path() -> Path:
    """Return the writable per-user database path, or NEO_DB_PATH if configured."""
    configured_path = os.getenv("NEO_DB_PATH")
    if configured_path:
        return Path(configured_path).expanduser().resolve()

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Neo" / "neo_database.db"

    # Useful for development on platforms without the Windows LOCALAPPDATA variable.
    return Path(__file__).resolve().parents[2] / "data" / "neo_database.db"


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Open a short-lived SQLite connection and commit or roll back atomically."""
    database_path = get_database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.create_function(
        "neo_casefold", 1, lambda value: value.casefold() if value else ""
    )
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    """Create the events table and indexes when the application starts."""
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL
                    CHECK (event_type IN ('task', 'meeting', 'reminder')),
                content TEXT NOT NULL,
                datetime_iso TEXT,
                created_at TEXT NOT NULL,
                is_completed INTEGER NOT NULL DEFAULT 0
                    CHECK (is_completed IN (0, 1))
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_datetime ON events(datetime_iso)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_type_datetime "
            "ON events(event_type, datetime_iso)"
        )
