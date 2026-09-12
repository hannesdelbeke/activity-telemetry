"""Sink validation and the HTTP contract the collector relies on."""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

TOKEN = "test-token"
os.environ["ACTIVITY_WRITE_TOKEN"] = TOKEN
os.environ.setdefault("ACTIVITY_DB", "/tmp/activity-sink-tests.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import activity_sink  # noqa: E402


def _event(**overrides) -> dict:
    event = {
        "event_id": "abc123",
        "schema_version": 1,
        "occurred_at": "2026-01-01T12:00:00+00:00",
        "machine_id": "test-machine",
        "event_type": "app_focus",
        "data": {"app": "example", "activity_state": "active"},
    }
    event.update(overrides)
    return event


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(_event(schema_version=2), id="wrong schema version"),
        pytest.param(_event(occurred_at="2026-01-01T12:00:00"), id="naive timestamp"),
        pytest.param(_event(occurred_at="not a timestamp"), id="unparseable timestamp"),
        pytest.param(_event(machine_id=""), id="empty machine id"),
        pytest.param(_event(data="not a dict"), id="data is not an object"),
        pytest.param({"event_id": "abc"}, id="missing required fields"),
        pytest.param("not an event", id="not an object"),
    ],
)
def test_invalid_events_are_rejected(event) -> None:
    assert activity_sink._valid_event(event) is False


def test_a_well_formed_event_is_accepted() -> None:
    assert activity_sink._valid_event(_event()) is True
    assert activity_sink._valid_event(_event(occurred_at="2026-01-01T12:00:00Z")) is True


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_sink, "DB_PATH", tmp_path / "activity.db")
    monkeypatch.setattr(activity_sink, "WRITE_TOKEN", TOKEN)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), activity_sink.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _post(url: str, body: bytes, token: str | None = TOKEN) -> int:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url + "/api/ingest", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_health_needs_no_token(server) -> None:
    with urllib.request.urlopen(server + "/health", timeout=5) as response:
        assert json.loads(response.read()) == {"status": "ok"}


def test_a_wrong_token_is_rejected(server) -> None:
    assert _post(server, b'{"events":[]}', token="wrong") == 401
    assert _post(server, b'{"events":[]}', token=None) == 401


def test_a_malformed_body_does_not_reach_the_client_as_a_500(server) -> None:
    assert _post(server, b"not json") == 400
    assert _post(server, b'{"events":"not a list"}') == 400
    assert _post(server, b"\xff\xfe not utf-8") == 400


def test_an_oversized_batch_is_rejected(server) -> None:
    batch = {"events": [_event(event_id=f"e{index}") for index in range(activity_sink.MAX_EVENTS + 1)]}
    assert _post(server, json.dumps(batch).encode()) == 400


def test_events_are_stored_once(server) -> None:
    import sqlite3

    body = json.dumps({"events": [_event()]}).encode()
    assert _post(server, body) == 202
    assert _post(server, body) == 202  # same event_id, replayed

    rows = sqlite3.connect(activity_sink.DB_PATH).execute("SELECT COUNT(*) FROM events").fetchone()
    assert rows[0] == 1


def _sample(minutes: int, app: str, state: str = "active", machine: str = "m1") -> dict:
    from datetime import datetime, timedelta, timezone

    return {
        "at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        "machine_id": machine,
        "app": app,
        "activity_state": state,
    }


def test_a_sample_is_charged_the_gap_to_the_next_one() -> None:
    samples = [_sample(0, "chrome"), _sample(1, "chrome"), _sample(2, "terminal")]
    summary = activity_sink._summarise(samples)
    # Two minute-long gaps: chrome holds both ends of the first, terminal is
    # last and so is charged nothing.
    assert dict(summary["apps"]) == {"chrome": 120.0, "terminal": 0.0}
    assert summary["samples"] == 3


def test_an_outage_is_not_counted_as_time_at_the_machine() -> None:
    steady = [_sample(minute, "chrome") for minute in range(5)]
    summary = activity_sink._summarise(steady + [_sample(600, "chrome")])
    # The five-minute run plus one capped gap, not the ten hours of downtime.
    assert summary["apps"][0][1] < 15 * 60


def test_machines_are_summarised_independently() -> None:
    samples = [
        _sample(0, "chrome", machine="mac"),
        _sample(1, "chrome", machine="mac"),
        _sample(0, "vim", machine="linux"),
        _sample(1, "vim", machine="linux"),
    ]
    summary = activity_sink._summarise(samples)
    assert summary["machines"] == ["linux", "mac"]
    assert dict(summary["apps"]) == {"chrome": 60.0, "vim": 60.0}


def test_an_empty_day_summarises_without_dividing_by_zero() -> None:
    assert activity_sink._summarise([]) == {"apps": [], "states": {}, "samples": 0, "machines": []}


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_sink, "DB_PATH", tmp_path / "activity.db")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), activity_sink.ReadHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_the_panel_renders_an_empty_day(panel) -> None:
    status, body = _get(panel + "/")
    assert status == 200
    assert b"No events for this day." in body


def test_an_unparseable_date_falls_back_to_today(panel) -> None:
    assert _get(panel + "/?date=banana")[0] == 200
    assert _get(panel + "/?date=2026-13-45")[0] == 200


def test_the_panel_escapes_an_app_name_from_a_client(panel, server) -> None:
    # The panel and the ingest server share DB_PATH through the same tmp_path.
    hostile = _event(event_id="xss", data={"app": "<script>alert(1)</script>", "activity_state": "active"})
    hostile["occurred_at"] = activity_sink.datetime.now(activity_sink.timezone.utc).isoformat()
    assert _post(server, json.dumps({"events": [hostile]}).encode()) == 202

    status, body = _get(panel + "/")
    assert status == 200
    assert b"<script>alert(1)</script>" not in body
    assert b"&lt;script&gt;" in body


def test_the_json_endpoint_returns_the_days_events(panel, server) -> None:
    event = _event(occurred_at=activity_sink.datetime.now(activity_sink.timezone.utc).isoformat())
    assert _post(server, json.dumps({"events": [event]}).encode()) == 202

    status, body = _get(panel + "/api/events")
    assert status == 200
    payload = json.loads(body)
    assert [item["app"] for item in payload["events"]] == ["example"]
