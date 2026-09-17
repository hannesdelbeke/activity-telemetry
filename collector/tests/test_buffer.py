"""Tests for the InactivityBuffer that delays emission only on unconfirmed inactivity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from activity_collector.buffer import InactivityBuffer


def _time(seconds: int = 0) -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def test_continuous_active_emits_immediately():
    buf = InactivityBuffer(idle_threshold_seconds=120.0)

    # First tick: user typing (idle_ms = 500)
    ready = buf.process("Ptyxis", "active", idle_ms=500, now=_time(0))
    assert len(ready) == 1
    assert ready[0].app == "Ptyxis"
    assert ready[0].activity_state == "active"

    # Second tick (30s later): user typing (idle_ms = 1200 < 30000)
    ready = buf.process("Ptyxis", "active", idle_ms=1200, now=_time(30))
    assert len(ready) == 1
    assert ready[0].app == "Ptyxis"
    assert ready[0].activity_state == "active"


def test_app_switch_emits_immediately():
    buf = InactivityBuffer(idle_threshold_seconds=120.0)
    buf.process("Ptyxis", "active", idle_ms=500, now=_time(0))

    # User switches to Firefox (idle_ms fresh, e.g. 200ms)
    ready = buf.process("Firefox", "active", idle_ms=200, now=_time(30))
    assert len(ready) == 1
    assert ready[0].app == "Firefox"
    assert ready[0].activity_state == "active"


def test_screen_lock_flushes_pending_as_active_and_emits_locked():
    buf = InactivityBuffer(idle_threshold_seconds=120.0)

    # Active tick
    buf.process("Ptyxis", "active", idle_ms=500, now=_time(0))

    # Countdown tick (user pauses typing)
    ready = buf.process("Ptyxis", "active", idle_ms=30_000, now=_time(30))
    assert len(ready) == 0  # Buffered

    # User locks the screen at t=60
    ready = buf.process("locked", "idle", idle_ms=0, now=_time(60))
    assert len(ready) == 2
    # The buffered sample before lock was active
    assert ready[0].activity_state == "active"
    assert ready[0].app == "Ptyxis"
    # The lock event is idle
    assert ready[1].activity_state == "idle"
    assert ready[1].app == "locked"


def test_thinking_pause_recovers_as_active_when_user_types():
    buf = InactivityBuffer(idle_threshold_seconds=120.0)

    # Active
    buf.process("Obsidian", "active", idle_ms=500, now=_time(0))

    # User pauses for 45s to think (at t=30, idle_ms=30s)
    ready = buf.process("Obsidian", "active", idle_ms=30_000, now=_time(30))
    assert len(ready) == 0  # Buffered unconfirmed

    # At t=45 user types; at t=60 idle_ms is 15s (< 30s)
    ready = buf.process("Obsidian", "active", idle_ms=15_000, now=_time(60))
    assert len(ready) == 2
    # Both the paused tick and the current tick are active
    assert ready[0].activity_state == "active"
    assert ready[0].occurred_at == _time(30).isoformat()
    assert ready[1].activity_state == "active"
    assert ready[1].occurred_at == _time(60).isoformat()


def test_break_departure_flips_buffered_countdown_to_idle():
    buf = InactivityBuffer(idle_threshold_seconds=120.0)

    # t=0: active
    ready = buf.process("Obsidian", "active", idle_ms=500, now=_time(0))
    assert len(ready) == 1

    # t=30: idle_ms=30s (buffered)
    assert buf.process("Obsidian", "active", idle_ms=30_000, now=_time(30)) == []

    # t=60: idle_ms=60s (buffered)
    assert buf.process("Obsidian", "active", idle_ms=60_000, now=_time(60)) == []

    # t=90: idle_ms=90s (buffered)
    assert buf.process("Obsidian", "active", idle_ms=90_000, now=_time(90)) == []

    # t=120: idle_ms=120s >= threshold (confirmed idle!)
    ready = buf.process("Obsidian", "idle", idle_ms=120_000, now=_time(120))
    assert len(ready) == 4
    # All 3 countdown samples and the 4th sample are emitted as idle!
    for sample in ready:
        assert sample.activity_state == "idle"
    assert [s.occurred_at for s in ready] == [
        _time(30).isoformat(),
        _time(60).isoformat(),
        _time(90).isoformat(),
        _time(120).isoformat(),
    ]


def test_ongoing_break_emits_idle_immediately():
    buf = InactivityBuffer(idle_threshold_seconds=120.0)
    # Trip the threshold
    buf.process("Obsidian", "active", idle_ms=500, now=_time(0))
    buf.process("Obsidian", "active", idle_ms=30_000, now=_time(30))
    buf.process("Obsidian", "active", idle_ms=60_000, now=_time(60))
    buf.process("Obsidian", "active", idle_ms=90_000, now=_time(90))
    buf.process("Obsidian", "idle", idle_ms=120_000, now=_time(120))

    # Next tick during the break (t=150, idle_ms=150s >= 120s)
    ready = buf.process("Obsidian", "idle", idle_ms=150_000, now=_time(150))
    assert len(ready) == 1
    assert ready[0].activity_state == "idle"
    assert ready[0].occurred_at == _time(150).isoformat()


def test_return_from_break_emits_active_immediately():
    buf = InactivityBuffer(idle_threshold_seconds=120.0)
    # Start during an ongoing break
    buf.process("Obsidian", "idle", idle_ms=300_000, now=_time(0))

    # User returns and types at t=30 (idle_ms=200ms)
    ready = buf.process("Obsidian", "active", idle_ms=200, now=_time(30))
    assert len(ready) == 1
    assert ready[0].activity_state == "active"
    assert ready[0].occurred_at == _time(30).isoformat()


def test_flush_all_empties_buffer():
    buf = InactivityBuffer(idle_threshold_seconds=120.0)
    buf.process("Obsidian", "active", idle_ms=500, now=_time(0))
    buf.process("Obsidian", "active", idle_ms=30_000, now=_time(30))
    buf.process("Obsidian", "active", idle_ms=60_000, now=_time(60))

    flushed = buf.flush_all()
    assert len(flushed) == 2
    assert len(buf.flush_all()) == 0
