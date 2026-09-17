"""Uploading to a sink that answers on more than one address.

The sink box here has a cabled interface and a wifi dongle. Rewiring the house
moves which of them is up, and the symptom when the collector knows only the
address that went away is not an error anyone sees -- it is a spool that grows
for a day and a half while every flush times out. These tests pin the two
things that stop that: a second address is tried, and the one that answered is
remembered so the dead one is not re-dialled every interval.
"""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from activity_collector import __main__ as entry  # noqa: E402
from activity_collector.spool import Spool  # noqa: E402

CABLE = "http://192.168.1.15:8788/api/ingest"
DONGLE = "http://192.168.1.204:8788/api/ingest"


def _event(event_id: str) -> dict:
    return {
        "event_id": event_id,
        "schema_version": 1,
        "occurred_at": "2026-01-01T12:00:00+00:00",
        "machine_id": "test-machine",
        "event_type": "app_focus",
        "data": {"app": "example", "activity_state": "active"},
    }


class _Sink:
    """Records every address dialled, and refuses the ones it is told to."""

    def __init__(self, *dead: str) -> None:
        self.dead = set(dead)
        self.tried: list[str] = []
        self.delivered: list[dict] = []

    def post(self, url: str, token: str, events: list[dict]) -> None:
        self.tried.append(url)
        if url in self.dead:
            raise urllib.error.URLError("timed out")
        self.delivered.extend(events)


@pytest.fixture
def spool(tmp_path: Path) -> Spool:
    spool = Spool(tmp_path / "events.db")
    spool.add(_event("a"))
    spool.add(_event("b"))
    return spool


def test_parses_a_comma_separated_list_in_order() -> None:
    assert entry._ingest_urls(f"{CABLE}, {DONGLE}") == [CABLE, DONGLE]


def test_a_single_address_is_still_accepted() -> None:
    assert entry._ingest_urls(CABLE) == [CABLE]


def test_unset_or_empty_means_no_addresses() -> None:
    assert entry._ingest_urls(None) == []
    assert entry._ingest_urls("  ") == []
    assert entry._ingest_urls(f"{CABLE},,") == [CABLE]


def test_the_backup_is_not_dialled_while_the_primary_answers(
    spool: Spool, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = _Sink()
    monkeypatch.setattr(entry, "_post", sink.post)

    accepted = entry._flush(spool, [CABLE, DONGLE], "token", 7)

    assert accepted == CABLE
    assert sink.tried == [CABLE]
    assert spool.pending() == []


def test_a_dead_primary_falls_through_to_the_backup(
    spool: Spool, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = _Sink(CABLE)
    monkeypatch.setattr(entry, "_post", sink.post)

    accepted = entry._flush(spool, [CABLE, DONGLE], "token", 7)

    assert accepted == DONGLE
    assert sink.tried == [CABLE, DONGLE]
    assert [event["event_id"] for event in sink.delivered] == ["a", "b"]
    assert spool.pending() == []


def test_events_stay_spooled_when_every_address_is_dead(
    spool: Spool, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = _Sink(CABLE, DONGLE)
    monkeypatch.setattr(entry, "_post", sink.post)

    with pytest.raises(entry.IngestUnreachable) as raised:
        entry._flush(spool, [CABLE, DONGLE], "token", 7)

    # The message has to name both, or diagnosing it means guessing which hop
    # broke from a log line that mentions only one address.
    assert CABLE in str(raised.value) and DONGLE in str(raised.value)
    assert len(spool.pending()) == 2


def test_the_address_that_answered_is_tried_first_next_time() -> None:
    urls = [CABLE, DONGLE]
    assert entry._prefer(urls, DONGLE) is True
    assert urls == [DONGLE, CABLE]


def test_preferring_the_address_already_in_front_changes_nothing() -> None:
    urls = [CABLE, DONGLE]
    assert entry._prefer(urls, CABLE) is False
    assert urls == [CABLE, DONGLE]


def test_nothing_pending_dials_nobody(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sink = _Sink()
    monkeypatch.setattr(entry, "_post", sink.post)

    assert entry._flush(Spool(tmp_path / "empty.db"), [CABLE], "token", 7) is None
    assert sink.tried == []


class _StubAdapter:
    def snapshot(self):
        from activity_collector.adapters import Snapshot

        return Snapshot("Safari", "active", 0)


def _run_main_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sink: _Sink):
    """Drive main() through exactly one interval and return its exit handler.

    The exit handler is worth holding on to: it flushes what the buffer is still
    sitting on when the machine shuts down, it runs nowhere else, and because it
    is a closure it can go on referring to a name the rest of main() has renamed
    without anything failing until the one moment it matters.
    """
    registered = []
    monkeypatch.setattr(entry.atexit, "register", registered.append)
    monkeypatch.setattr(entry, "create_adapter", lambda: _StubAdapter())
    monkeypatch.setattr(entry, "_post", sink.post)
    monkeypatch.setattr(entry, "sleep_interval", lambda _: (_ for _ in ()).throw(SystemExit(0)))
    monkeypatch.setenv("ACTIVITY_MACHINE_ID", "test-machine")
    monkeypatch.setenv("ACTIVITY_SPOOL", str(tmp_path / "events.db"))
    monkeypatch.setenv("ACTIVITY_INGEST_URL", f"{CABLE},{DONGLE}")
    monkeypatch.setenv("ACTIVITY_WRITE_TOKEN", "token")

    with pytest.raises(SystemExit):
        entry.main()
    return registered


def test_main_uploads_through_the_address_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = _Sink(CABLE)
    _run_main_once(tmp_path, monkeypatch, sink)

    assert sink.tried == [CABLE, DONGLE]


def test_the_shutdown_flush_uses_the_same_address_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = _Sink()
    registered = _run_main_once(tmp_path, monkeypatch, sink)

    assert registered, "main() registered no exit handler"
    sink.tried.clear()
    registered[0]()  # must not raise NameError on a renamed local

    assert sink.tried in ([], [CABLE]), sink.tried
