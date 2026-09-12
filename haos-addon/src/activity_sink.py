"""Minimal append-only activity ingest service using the Python standard library."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 256 * 1024
MAX_EVENTS = 100
DB_PATH = Path(os.environ.get("ACTIVITY_DB", "/data/activity.db"))
def _option(name: str) -> str | None:
    value = os.environ.get("ACTIVITY_WRITE_TOKEN") if name == "write_token" else os.environ.get(name)
    if value:
        return value
    try:
        options = json.loads(Path("/data/options.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    option = options.get(name)
    return option if isinstance(option, str) and option else None


WRITE_TOKEN = _option("write_token")


def _database() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            occurred_at TEXT NOT NULL,
            machine_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            received_at TEXT NOT NULL
        )
        """
    )
    return db


def _valid_event(event: object) -> bool:
    if not isinstance(event, dict):
        return False
    required = {"event_id", "schema_version", "occurred_at", "machine_id", "event_type", "data"}
    if not required.issubset(event) or event["schema_version"] != 1:
        return False
    if not all(isinstance(event[key], str) and event[key] for key in ("event_id", "occurred_at", "machine_id", "event_type")):
        return False
    if not isinstance(event["data"], dict):
        return False
    try:
        parsed = datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


class Handler(BaseHTTPRequestHandler):
    server_version = "activity-sink/0.1"

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
            return
        self._respond(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/api/ingest":
            self._respond(404, {"error": "not_found"})
            return
        if not WRITE_TOKEN or self.headers.get("Authorization") != f"Bearer {WRITE_TOKEN}":
            self._respond(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            self._respond(413, {"error": "invalid_body_size"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid_json"})
            return
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list) or len(events) > MAX_EVENTS or not all(_valid_event(event) for event in events):
            self._respond(400, {"error": "invalid_events"})
            return
        received_at = datetime.now(timezone.utc).isoformat()
        with _database() as db:
            for event in events:
                db.execute(
                    """
                    INSERT OR IGNORE INTO events
                    (event_id, schema_version, occurred_at, machine_id, event_type, payload_json, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["event_id"],
                        event["schema_version"],
                        event["occurred_at"],
                        event["machine_id"],
                        event["event_type"],
                        json.dumps(event, separators=(",", ":")),
                        received_at,
                    ),
                )
        self._respond(202, {"accepted": len(events)})


def main() -> None:
    port = int(os.environ.get("ACTIVITY_PORT", "8788"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
