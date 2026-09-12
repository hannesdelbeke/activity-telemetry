"""Minimal append-only activity ingest service using the Python standard library."""

from __future__ import annotations

import hmac
import html
import json
import logging
import os
import sqlite3
import statistics
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MAX_BODY = 256 * 1024
MAX_EVENTS = 100
MAX_READ_EVENTS = 5000
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


@contextmanager
def _database() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout, because ThreadingHTTPServer can land two writers at once and the
    # sqlite default gives up after five seconds with "database is locked".
    db = sqlite3.connect(DB_PATH, timeout=30)
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
    try:
        with db:
            yield db
    finally:
        db.close()


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


def _local_zone() -> timezone:
    """The add-on inherits TZ from Home Assistant, so days match the user's clock."""
    return datetime.now().astimezone().tzinfo or timezone.utc


def _read_day(day: date, machine: str | None) -> list[dict]:
    """Events overlapping one local day, oldest first."""
    zone = _local_zone()
    start = datetime.combine(day, datetime.min.time(), zone)
    end = start + timedelta(days=1)
    # occurred_at is ISO text, so the SQL bound is a coarse string filter with a
    # day of slack on each side; clients in other offsets are then dropped below
    # by the real comparison on parsed datetimes.
    with _database() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT occurred_at, machine_id, payload_json FROM events "
            "WHERE occurred_at >= ? AND occurred_at < ? "
            + ("AND machine_id = ? " if machine else "")
            + "ORDER BY occurred_at LIMIT ?",
            ((start - timedelta(days=1)).isoformat(), (end + timedelta(days=1)).isoformat())
            + ((machine,) if machine else ())
            + (MAX_READ_EVENTS,),
        ).fetchall()

    events = []
    for row in rows:
        moment = datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))
        if not start <= moment < end:
            continue
        data = json.loads(row["payload_json"]).get("data", {})
        events.append(
            {
                "at": moment.astimezone(zone),
                "machine_id": row["machine_id"],
                "app": str(data.get("app", "unknown")),
                "activity_state": str(data.get("activity_state", "unknown")),
            }
        )
    return events


def _summarise(events: list[dict]) -> dict:
    """Turn point samples into durations by charging each sample the gap to the next.

    A sample says only "this app was in front at this instant". The gap to the
    next sample is capped, because a laptop that closes at noon and reopens at
    five should not report five hours of whatever was on screen at noon.
    """
    per_machine: dict[str, list[dict]] = {}
    for event in events:
        per_machine.setdefault(event["machine_id"], []).append(event)

    apps: dict[str, float] = {}
    states: dict[str, float] = {}
    for samples in per_machine.values():
        gaps = [
            (later["at"] - earlier["at"]).total_seconds()
            for earlier, later in zip(samples, samples[1:])
        ]
        # Cap at three intervals, so one missed beat still counts but an outage
        # does not. The interval is inferred rather than configured, because the
        # sink never learns what the collector was told to use.
        cap = min(900.0, max(60.0, 3 * statistics.median(gaps))) if gaps else 60.0
        for sample, gap in zip(samples, gaps + [0.0]):
            charged = min(gap, cap)
            apps[sample["app"]] = apps.get(sample["app"], 0.0) + charged
            states[sample["activity_state"]] = states.get(sample["activity_state"], 0.0) + charged

    return {
        "apps": sorted(apps.items(), key=lambda item: -item[1]),
        "states": states,
        "samples": len(events),
        "machines": sorted(per_machine),
    }


def _duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def _page(day: date, machine: str | None, summary: dict, recent: list[dict]) -> bytes:
    longest = max((seconds for _, seconds in summary["apps"]), default=0.0) or 1.0
    keep = {"machine": machine} if machine else {}

    def link(**params: object) -> str:
        query = "&".join(f"{key}={html.escape(str(value))}" for key, value in {**keep, **params}.items())
        return html.escape(f"?{query}" if query else "?")

    rows = "".join(
        f'<tr><td>{html.escape(app)}</td><td class="n">{_duration(seconds)}</td>'
        f'<td class="b"><span style="width:{seconds / longest * 100:.1f}%"></span></td></tr>'
        for app, seconds in summary["apps"]
    ) or '<tr><td colspan="3" class="empty">No events for this day.</td></tr>'

    tail = "".join(
        f'<tr><td class="n">{event["at"]:%H:%M:%S}</td><td>{html.escape(event["app"])}</td>'
        f'<td>{html.escape(event["activity_state"])}</td><td>{html.escape(event["machine_id"])}</td></tr>'
        for event in recent
    )

    tabs = "".join(
        f'<a class="tab{" on" if name == machine else ""}" href="{html.escape(f"?date={day}&machine={name}")}">{html.escape(name)}</a>'
        for name in summary["machines"]
    )
    if machine:
        tabs = f'<a class="tab" href="{html.escape(f"?date={day}")}">all</a>' + tabs
    elif tabs:
        tabs = '<a class="tab on" href="?">all</a>' + tabs

    return f"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Activity {day}</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0 auto; padding: 1.5rem; max-width: 46rem; }}
 h1 {{ font-size: 1.3rem; margin: 0 0 .25rem; }}
 nav {{ display: flex; gap: .75rem; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; }}
 a {{ color: inherit; }}
 .tab {{ text-decoration: none; opacity: .55; }}
 .tab.on {{ opacity: 1; font-weight: 600; }}
 .lede {{ opacity: .7; margin: 0 0 1.25rem; }}
 table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
 td, th {{ text-align: left; padding: .3rem .5rem .3rem 0; border-bottom: 1px solid color-mix(in srgb, currentColor 12%, transparent); }}
 th {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; opacity: .55; }}
 .n {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
 .b {{ width: 45%; }}
 .b span {{ display: block; height: .55rem; border-radius: .3rem; background: currentColor; opacity: .35; }}
 .empty {{ opacity: .6; padding: 1rem 0; }}
</style>
<h1>Activity</h1>
<nav>
 <a href="{link(date=day - timedelta(days=1))}">&larr;</a>
 <strong>{day:%a %d %b %Y}</strong>
 <a href="{link(date=day + timedelta(days=1))}">&rarr;</a>
 <span style="flex:1"></span>{tabs}
</nav>
<p class="lede">
 {_duration(summary["states"].get("active", 0.0))} active
 &middot; {_duration(summary["states"].get("idle", 0.0))} idle
 &middot; {summary["samples"]} samples
</p>
<table><tr><th>App</th><th>Time</th><th></th></tr>{rows}</table>
{f'<table><tr><th>Last seen</th><th>App</th><th>State</th><th>Machine</th></tr>{tail}</table>' if tail else ''}
""".encode()


class ReadHandler(BaseHTTPRequestHandler):
    """The Ingress side. Home Assistant authenticates it, so there is no token here.

    This listens on INGRESS_PORT, which config.yaml deliberately does not publish:
    it is reachable only from the Supervisor's own network, never from the LAN.
    Nothing here writes, and the ingest port serves none of it, so a leaked
    device write token still cannot read anybody's history back out.
    """

    server_version = "activity-sink/0.2"

    def log_message(self, fmt: str, *args: object) -> None:
        logging.debug("ingress %s", fmt % args)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        machine = (query.get("machine") or [None])[0]
        try:
            day = date.fromisoformat(query["date"][0]) if "date" in query else None
        except (ValueError, IndexError):
            day = None
        if day is None:
            day = datetime.now(_local_zone()).date()

        try:
            events = _read_day(day, machine)
        except sqlite3.Error:
            logging.exception("could not read %s", DB_PATH)
            self._send(503, b'{"error":"storage_unavailable"}', "application/json")
            return

        if url.path.rstrip("/").endswith("/api/events"):
            body = json.dumps(
                {"date": day.isoformat(), "events": [{**e, "at": e["at"].isoformat()} for e in events]}
            ).encode()
            self._send(200, body, "application/json")
            return

        # The machine tabs have to list every machine seen that day, so the
        # summary is computed before the filter narrows the table.
        summary = _summarise(events)
        summary["machines"] = _summarise(_read_day(day, None))["machines"]
        self._send(200, _page(day, machine, summary, events[-12:][::-1]), "text/html; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "activity-sink/0.2"

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
        # compare_digest rather than ==, so a wrong token cannot be recovered a
        # byte at a time from how long the comparison took.
        offered = self.headers.get("Authorization", "")
        if not WRITE_TOKEN or not hmac.compare_digest(offered, f"Bearer {WRITE_TOKEN}"):
            self._respond(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            # A non-numeric header used to raise out of the handler as a 500.
            self._respond(400, {"error": "invalid_content_length"})
            return
        if length <= 0:
            self._respond(400, {"error": "empty_body"})
            return
        if length > MAX_BODY:
            self._respond(413, {"error": "body_too_large"})
            return
        try:
            # ValueError, not JSONDecodeError: a non-UTF-8 body raises
            # UnicodeDecodeError here, which used to reach the client as a 500.
            payload = json.loads(self.rfile.read(length))
        except ValueError:
            self._respond(400, {"error": "invalid_json"})
            return
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list) or len(events) > MAX_EVENTS or not all(_valid_event(event) for event in events):
            self._respond(400, {"error": "invalid_events"})
            return
        received_at = datetime.now(timezone.utc).isoformat()
        try:
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
        except sqlite3.Error:
            # 503, not 202: the collector keeps the batch spooled and retries,
            # where a false 202 would drop it.
            logging.exception("could not write %s events to %s", len(events), DB_PATH)
            self._respond(503, {"error": "storage_unavailable"})
            return
        self._respond(202, {"accepted": len(events)})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.environ.get("ACTIVITY_PORT", "8788"))
    ingress_port = int(os.environ.get("ACTIVITY_INGRESS_PORT", "8099"))
    if not WRITE_TOKEN:
        # Previously this started and rejected every request with 401, with
        # nothing in the log saying why.
        logging.warning("no write_token set, every ingest request will be rejected with 401")

    # Two servers rather than one, so reading and writing keep separate doors:
    # ingest is published to the LAN behind a device token, the panel is not
    # published at all and is reachable only through Home Assistant's Ingress.
    panel = ThreadingHTTPServer(("0.0.0.0", ingress_port), ReadHandler)
    threading.Thread(target=panel.serve_forever, daemon=True).start()
    logging.info("ingress panel on 0.0.0.0:%s", ingress_port)

    logging.info("listening on 0.0.0.0:%s, storing events in %s", port, DB_PATH)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
