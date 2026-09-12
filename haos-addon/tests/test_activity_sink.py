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
