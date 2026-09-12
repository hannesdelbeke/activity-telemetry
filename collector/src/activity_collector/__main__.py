"""Minimal local collector entry point."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .adapters import create_adapter, sleep_interval
from .spool import Spool


def _event(machine_id: str, app: str, state: str) -> dict:
    occurred_at = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(f"{machine_id}:{occurred_at}:{app}:{state}".encode()).hexdigest()
    return {
        "event_id": digest,
        "schema_version": 1,
        "occurred_at": occurred_at,
        "machine_id": machine_id,
        "event_type": "app_focus",
        "data": {"app": app, "activity_state": state},
    }


def _post(url: str, token: str, events: list[dict]) -> None:
    body = json.dumps({"events": events}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status not in (200, 201, 202):
            raise RuntimeError(f"ingest returned HTTP {response.status}")


def main() -> None:
    machine_id = os.environ["ACTIVITY_MACHINE_ID"]
    spool_path = Path(
        os.environ.get(
            "ACTIVITY_SPOOL",
            Path.home() / ".cache" / "activity-collector" / "events.db",
        )
    )
    spool = Spool(spool_path)
    interval = int(os.environ.get("ACTIVITY_INTERVAL_SECONDS", "30"))
    ingest_url = os.environ.get("ACTIVITY_INGEST_URL")
    token = os.environ.get("ACTIVITY_WRITE_TOKEN")

    adapter = create_adapter()
    while True:
        snapshot = adapter.snapshot()
        spool.add(_event(machine_id, snapshot.app, snapshot.activity_state))
        pending = spool.pending()
        if ingest_url and token and pending:
            events = [item["event"] for item in pending]
            _post(ingest_url, token, events)
            spool.mark_synced(item["event_id"] for item in pending)
        sleep_interval(interval)


if __name__ == "__main__":
    main()
