"""Local-first SQLite event spool."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable


class Spool:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    machine_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    synced_at TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def add(self, event: dict) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO events
                (event_id, occurred_at, machine_id, event_type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["occurred_at"],
                    event["machine_id"],
                    event["event_type"],
                    json.dumps(event, separators=(",", ":")),
                ),
            )

    def pending(self, limit: int = 100) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT event_id, payload_json FROM events "
                "WHERE synced_at IS NULL ORDER BY occurred_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"event_id": row[0], "event": json.loads(row[1])} for row in rows]

    def mark_synced(self, event_ids: Iterable[str]) -> None:
        with self._connect() as db:
            db.executemany(
                "UPDATE events SET synced_at = datetime('now') WHERE event_id = ?",
                ((event_id,) for event_id in event_ids),
            )
