"""Spool behaviour the collector's offline guarantee rests on."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from activity_collector.spool import Spool  # noqa: E402


def _event(event_id: str, occurred_at: str = "2026-01-01T12:00:00+00:00") -> dict:
    return {
        "event_id": event_id,
        "schema_version": 1,
        "occurred_at": occurred_at,
        "machine_id": "test-machine",
        "event_type": "app_focus",
        "data": {"app": "example", "activity_state": "active"},
    }


def test_add_is_idempotent(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "events.db")
    spool.add(_event("a"))
    spool.add(_event("a"))
    assert len(spool.pending()) == 1


def test_unsent_events_stay_pending(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "events.db")
    spool.add(_event("a"))
    spool.add(_event("b"))
    spool.mark_synced(["a"])
    assert [item["event_id"] for item in spool.pending()] == ["b"]


def test_prune_keeps_unsent_events(tmp_path: Path) -> None:
    """The prune must never be able to drop an event the sink has not confirmed."""
    spool = Spool(tmp_path / "events.db")
    spool.add(_event("sent"))
    spool.add(_event("unsent"))
    spool.mark_synced(["sent"])
    # Backdate the synced row past the retention window.
    with sqlite3.connect(tmp_path / "events.db") as db:
        db.execute("UPDATE events SET synced_at = datetime('now', '-30 days') WHERE event_id = 'sent'")

    assert spool.prune_synced(7) == 1
    assert [item["event_id"] for item in spool.pending()] == ["unsent"]


def test_prune_spares_recently_synced_events(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "events.db")
    spool.add(_event("a"))
    spool.mark_synced(["a"])
    assert spool.prune_synced(7) == 0


def test_pending_is_ordered_and_bounded(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "events.db")
    for index in range(5):
        spool.add(_event(f"e{index}", f"2026-01-01T12:00:0{index}+00:00"))
    assert [item["event_id"] for item in spool.pending(limit=3)] == ["e0", "e1", "e2"]
