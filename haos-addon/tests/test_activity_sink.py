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


def test_a_bar_starts_where_its_sample_landed_and_runs_for_the_charged_gap() -> None:
    from datetime import date

    samples = [_sample(0, "chrome"), _sample(60, "chrome"), _sample(120, "terminal")]
    (machine, bars), = activity_sink._segments(samples, date(2026, 1, 1))

    from datetime import datetime as dt

    assert machine == "m1"
    # Midnight UTC is not midnight locally, so the expected position is measured
    # from the same local midnight the page renders against rather than assumed
    # to be zero. Hardcoding 0.0 here passes only in UTC.
    local = samples[0]["at"].astimezone(activity_sink._local_zone())
    midnight = dt.combine(date(2026, 1, 1), dt.min.time(), activity_sink._local_zone())
    assert bars[0]["left"] == pytest.approx((local - midnight).total_seconds() / 864.0)
    # The gap to the next sample is an hour, but _cap tops a single sample out
    # at 900s, so the bar is fifteen minutes of the 24 hour track and not an
    # hour of it. A bar wider than its cap is the visual form of the outage bug
    # the summary already guards against.
    assert bars[0]["width"] == pytest.approx(900 / 864.0)
    # Two bars, not three: the last sample of a machine is charged nothing.
    assert len(bars) == 2


def test_the_timeline_and_the_summary_agree_on_how_long_things_took() -> None:
    """The two views are the same numbers drawn differently, so they share _cap.

    They used to hold the rule twice, once in Python and once in JavaScript.
    This is the test that would have caught the copies drifting.
    """
    from datetime import date

    samples = [_sample(minute, "chrome") for minute in range(5)] + [_sample(600, "chrome")]
    summary = dict(activity_sink._summarise(samples)["apps"])
    drawn = sum(bar["seconds"] for _, bars in activity_sink._segments(samples, date(2026, 1, 1)) for bar in bars)
    assert drawn == pytest.approx(summary["chrome"])


def test_a_bar_is_clipped_at_midnight_rather_than_overhanging_the_track() -> None:
    from datetime import date

    # A sample just before midnight whose capped gap would run past it.
    samples = [_sample(23 * 60 + 59, "chrome"), _sample(24 * 60 + 30, "chrome")]
    (_, bars), = activity_sink._segments(samples, date(2026, 1, 1))
    for bar in bars:
        assert bar["left"] + bar["width"] <= 100.0 + 1e-9


def test_touching_bars_of_one_state_become_a_single_run() -> None:
    from datetime import date

    steady = [_sample(minute, "chrome") for minute in range(11)]
    (_, bars), = activity_sink._segments(steady, date(2026, 1, 1))
    # Ten charged minute-long bars, one run. Unmerged these are a quarter of a
    # pixel each and render as a smear rather than a band.
    assert len(bars) == 1
    assert bars[0]["seconds"] == pytest.approx(10 * 60)


def test_a_change_of_state_breaks_the_run() -> None:
    from datetime import date

    samples = [_sample(m, "chrome", "active") for m in range(3)]
    samples += [_sample(m, "chrome", "idle") for m in range(3, 6)]
    (_, bars), = activity_sink._segments(samples, date(2026, 1, 1))
    assert [bar["state"] for bar in bars] == ["active", "idle"]


def test_an_outage_stays_a_hole_instead_of_being_merged_over() -> None:
    """The gap is the point. Merging across it paints an absence as presence."""
    from datetime import date

    samples = [_sample(0, "chrome"), _sample(1, "chrome"), _sample(600, "chrome"), _sample(601, "chrome")]
    (_, bars), = activity_sink._segments(samples, date(2026, 1, 1))
    assert len(bars) > 1
    # Every run ends before the next begins; none spans the ten hour outage.
    for earlier, later in zip(bars, bars[1:]):
        assert earlier["left"] + earlier["width"] < later["left"]


def test_an_empty_day_draws_no_tracks() -> None:
    from datetime import date

    assert activity_sink._segments([], date(2026, 1, 1)) == []


def test_the_timeline_renders_and_links_stay_relative(panel, server) -> None:
    event = _event(occurred_at=activity_sink.datetime.now(activity_sink.timezone.utc).isoformat())
    assert _post(server, json.dumps({"events": [event]}).encode()) == 202

    status, body = _get(panel + "/?view=timeline")
    assert status == 200
    assert b'class="track"' in body
    # Ingress mounts the panel under a generated prefix, so an href starting at
    # the root leaves the add-on and hits Home Assistant's own endpoints.
    assert b'href="/' not in body


def test_the_summary_and_the_timeline_link_to_each_other(panel) -> None:
    assert b"view=timeline" in _get(panel + "/")[1]
    assert b"Summary" in _get(panel + "/?view=timeline")[1]


def test_the_timeline_escapes_hostile_values_from_a_client(panel, server) -> None:
    hostile = _event(
        event_id="xss-timeline",
        machine_id='m"><script>alert(1)</script>',
        data={"app": '<img src=x onerror=alert(1)>', "activity_state": '"><script>alert(1)</script>'},
    )
    hostile["occurred_at"] = activity_sink.datetime.now(activity_sink.timezone.utc).isoformat()
    assert _post(server, json.dumps({"events": [hostile]}).encode()) == 202

    body = _get(panel + "/?view=timeline")[1]
    assert b"<script>alert(1)</script>" not in body
    assert b"onerror=alert(1)" not in body


def test_a_machine_name_with_an_ampersand_stays_one_parameter(panel, server) -> None:
    # Unencoded, `a&b` would split the href into a second query parameter and
    # the tab would filter on `a` instead.
    event = _event(event_id="amp", machine_id="a&b")
    event["occurred_at"] = activity_sink.datetime.now(activity_sink.timezone.utc).isoformat()
    assert _post(server, json.dumps({"events": [event]}).encode()) == 202

    body = _get(panel + "/")[1].decode()
    assert "machine=a%26b" in body


def _forget_request(url: str, machine: str) -> int:
    import urllib.parse

    body = urllib.parse.urlencode({"forget": machine}).encode()
    request = urllib.request.Request(url + "/", data=body, method="POST")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):  # the 303 is the success signal
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def _stored(machine: str) -> int:
    import sqlite3

    db = sqlite3.connect(activity_sink.DB_PATH)
    try:
        return db.execute("SELECT COUNT(*) FROM events WHERE machine_id = ?", (machine,)).fetchone()[0]
    finally:
        db.close()


def test_forgetting_a_machine_removes_only_that_machines_events(panel, server) -> None:
    now = activity_sink.datetime.now(activity_sink.timezone.utc).isoformat()
    for index, machine in enumerate(["keep-me", "smoke-test", "smoke-test"]):
        event = _event(event_id=f"f{index}", machine_id=machine, occurred_at=now)
        assert _post(server, json.dumps({"events": [event]}).encode()) == 202

    assert _stored("smoke-test") == 2
    assert _forget_request(panel, "smoke-test") == 303
    assert _stored("smoke-test") == 0
    assert _stored("keep-me") == 1


def test_forgetting_needs_a_post_so_a_prefetch_cannot_delete(panel, server) -> None:
    event = _event(event_id="prefetch", machine_id="safe")
    event["occurred_at"] = activity_sink.datetime.now(activity_sink.timezone.utc).isoformat()
    assert _post(server, json.dumps({"events": [event]}).encode()) == 202

    # A GET carrying the same parameter must render a page, never delete.
    assert _get(panel + "/?forget=safe")[0] == 200
    assert _stored("safe") == 1


def test_the_ingest_port_cannot_delete_anything(server) -> None:
    """A leaked device write token adds events. It must never remove them."""
    event = _event(event_id="ingest-delete", machine_id="safe2")
    event["occurred_at"] = activity_sink.datetime.now(activity_sink.timezone.utc).isoformat()
    assert _post(server, json.dumps({"events": [event]}).encode()) == 202

    assert _forget_request(server, "safe2") in (401, 404)
    assert _stored("safe2") == 1


def test_a_forget_with_no_machine_is_rejected(panel) -> None:
    assert _forget_request(panel, "") == 400


def test_the_timeline_offers_a_forget_button_per_machine(panel, server) -> None:
    event = _event(event_id="btn", machine_id="m-btn")
    event["occurred_at"] = activity_sink.datetime.now(activity_sink.timezone.utc).isoformat()
    assert _post(server, json.dumps({"events": [event]}).encode()) == 202

    body = _get(panel + "/?view=timeline")[1].decode()
    assert 'name="forget" value="m-btn"' in body
    assert 'method="post"' in body


def test_the_json_endpoint_returns_the_days_events(panel, server) -> None:
    event = _event(occurred_at=activity_sink.datetime.now(activity_sink.timezone.utc).isoformat())
    assert _post(server, json.dumps({"events": [event]}).encode()) == 202

    status, body = _get(panel + "/api/events")
    assert status == 200
    payload = json.loads(body)
    assert [item["app"] for item in payload["events"]] == ["example"]
