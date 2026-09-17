"""Inactivity buffer that delays emission only during unconfirmed idle countdowns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class CandidateSample:
    occurred_at: str
    app: str
    activity_state: str


class InactivityBuffer:
    """Buffers samples only when user inactivity is ambiguous (countdown to idle threshold).

    Active events (typing, app swaps, returning from break), locked screens, and
    established idle periods are emitted with zero delay.
    """

    def __init__(self, idle_threshold_seconds: float = 120.0):
        self.idle_threshold_seconds = idle_threshold_seconds
        self._queue: list[CandidateSample] = []
        self._last_tick_time: datetime | None = None

    def process(
        self,
        app: str,
        state: str,
        idle_ms: int | None = None,
        now: datetime | None = None,
    ) -> list[CandidateSample]:
        """Process one sample tick and return any samples confirmed ready to write."""
        if now is None:
            now = datetime.now(timezone.utc)
        occurred_at = now.isoformat()

        # Elapsed time since the previous sample
        if self._last_tick_time is not None:
            elapsed_ms = (now - self._last_tick_time).total_seconds() * 1000.0
        else:
            elapsed_ms = 30000.0
        self._last_tick_time = now

        # 1. Screen is locked: immediate emission; any preceding samples were active
        if app == "locked":
            ready = []
            for item in self._queue:
                item.activity_state = "active"
                ready.append(item)
            self._queue.clear()
            ready.append(CandidateSample(occurred_at, app, "idle"))
            return ready

        # 2. Probe declined / unknown state: flush queue as-is and emit unknown
        if state == "unknown" or idle_ms is None:
            ready = list(self._queue)
            self._queue.clear()
            ready.append(CandidateSample(occurred_at, app, state))
            return ready

        # 3. Active input detected (user interacted within this sample interval)
        # Requires idle_ms < elapsed_ms (with 100ms clock drift margin)
        if idle_ms < max(0.0, elapsed_ms - 100.0):
            ready = []
            for item in self._queue:
                item.activity_state = "active"
                ready.append(item)
            self._queue.clear()
            ready.append(CandidateSample(occurred_at, app, "active"))
            return ready

        # 4. Confirmed idle: threshold breached
        if idle_ms >= self.idle_threshold_seconds * 1000.0:
            ready = []
            for item in self._queue:
                item.activity_state = "idle"
                ready.append(item)
            self._queue.clear()
            ready.append(CandidateSample(occurred_at, app, "idle"))
            return ready

        # 5. Ambiguous countdown: user untouched during this interval, but < threshold
        self._queue.append(CandidateSample(occurred_at, app, "active"))
        return []

    def flush_all(self) -> list[CandidateSample]:
        """Flush all pending samples on shutdown/restart."""
        ready = list(self._queue)
        self._queue.clear()
        return ready
