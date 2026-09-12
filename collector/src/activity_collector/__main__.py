"""Minimal local collector entry point."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
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
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status not in (200, 201, 202):
            raise RuntimeError(f"ingest returned HTTP {response.status}")


def _flush(spool: Spool, ingest_url: str, token: str, retention_days: int) -> None:
    """Upload one batch. Anything not confirmed stays spooled for the next pass."""
    pending = spool.pending()
    if not pending:
        return
    _post(ingest_url, token, [item["event"] for item in pending])
    spool.mark_synced(item["event_id"] for item in pending)
    spool.prune_synced(retention_days)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    machine_id = os.environ.get("ACTIVITY_MACHINE_ID")
    if not machine_id:
        raise SystemExit("ACTIVITY_MACHINE_ID is required, and should be a generic device label")
    spool_path = Path(
        os.environ.get(
            "ACTIVITY_SPOOL",
            Path.home() / ".cache" / "activity-collector" / "events.db",
        )
    )
    spool = Spool(spool_path)
    interval = int(os.environ.get("ACTIVITY_INTERVAL_SECONDS", "30"))
    retention_days = int(os.environ.get("ACTIVITY_SPOOL_RETENTION_DAYS", "7"))
    ingest_url = os.environ.get("ACTIVITY_INGEST_URL")
    token = os.environ.get("ACTIVITY_WRITE_TOKEN")
    if not (ingest_url and token):
        logging.info("no ingest URL and write token, spooling locally to %s only", spool_path)

    adapter = create_adapter()
    while True:
        # A daemon that dies on a transient fault collects nothing until someone
        # notices, which is the failure the local spool exists to prevent. Every
        # step below is therefore allowed to fail without ending the loop.
        try:
            snapshot = adapter.snapshot()
            spool.add(_event(machine_id, snapshot.app, snapshot.activity_state))
        except Exception:
            logging.exception("snapshot failed, skipping this interval")

        if ingest_url and token:
            try:
                _flush(spool, ingest_url, token, retention_days)
            except (OSError, urllib.error.URLError, RuntimeError) as error:
                # Unsent events keep synced_at NULL, so the next pass retries them.
                logging.warning("ingest failed, %s events still spooled: %s", len(spool.pending()), error)
            except Exception:
                logging.exception("ingest failed unexpectedly, events still spooled")

        sleep_interval(interval)


if __name__ == "__main__":
    main()
