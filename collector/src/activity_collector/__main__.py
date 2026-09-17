"""Minimal local collector entry point."""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import signal
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .adapters import IDLE_AFTER_SECONDS, create_adapter, sleep_interval
from .buffer import InactivityBuffer
from .spool import Spool


def _event(machine_id: str, app: str, state: str, occurred_at: str | None = None) -> dict:
    if occurred_at is None:
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


class IngestUnreachable(RuntimeError):
    """No configured ingest address accepted the batch."""


def _ingest_urls(raw: str | None) -> list[str]:
    """The configured ingest addresses, most preferred first.

    One sink can answer on more than one address -- ours has a cabled interface
    and a wifi dongle -- and which of them is up is exactly what an afternoon of
    rewiring changes. `ACTIVITY_INGEST_URL` therefore takes a comma-separated
    list, so moving the box costs a reordering instead of a silent outage. A
    single address parses as a list of one, which is what every old config is.
    """
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _prefer(urls: list[str], accepted: str) -> bool:
    """Move the address that answered to the front, in place; say whether it moved.

    Without this a dead primary costs a full connect timeout on every interval
    for as long as it stays dead, which at a 30s interval is most of the
    interval spent dialling a machine that is not there.
    """
    if accepted not in urls or urls[0] == accepted:
        return False
    urls.remove(accepted)
    urls.insert(0, accepted)
    return True


def _flush(spool: Spool, ingest_urls: list[str], token: str, retention_days: int) -> str | None:
    """Upload one batch, trying each address in turn.

    Returns the address that accepted it, or None when there was nothing to
    send. Anything not confirmed stays spooled for the next pass.
    """
    pending = spool.pending()
    if not pending:
        return None
    events = [item["event"] for item in pending]
    failures = []
    for url in ingest_urls:
        try:
            _post(url, token, events)
        except (OSError, urllib.error.URLError, RuntimeError) as error:
            failures.append(f"{url}: {error}")
            continue
        spool.mark_synced(item["event_id"] for item in pending)
        spool.prune_synced(retention_days)
        return url
    raise IngestUnreachable("; ".join(failures))


def diagnose() -> int:
    """Print one snapshot and say which idle probe produced it.

    Exists because the failure mode this collector is most prone to is silent:
    when every idle probe declines, the result looks exactly like a probe that
    answered, and the only symptom is a machine that is never idle. Reading the
    spool cannot tell you which -- you have to ask the adapter what spoke.

    Returns a shell exit code: non-zero when no probe could measure idle time, so
    this is usable as a check and not only as something to read.
    """
    adapter = create_adapter()
    snapshot = adapter.snapshot()
    source, idle_ms = getattr(adapter, "idle_reading", lambda: ("n/a", None))()

    print(f"adapter       {type(adapter).__name__}")
    print(f"app           {snapshot.app}")
    print(f"activity      {snapshot.activity_state}")
    print(f"idle source   {source}")
    print(f"idle ms       {idle_ms if idle_ms is not None else '-'}")

    if snapshot.activity_state == "unknown":
        print(
            "\nNo idle probe answered, so activity is reported as unknown rather\n"
            "than guessed. On GNOME Wayland this usually means the shell extension\n"
            "is not enabled: check `gnome-extensions list --enabled` for\n"
            "activity-collector@local, and see AGENTS.md."
        )
        return 1
    return 0


def main() -> None:
    if "--diagnose" in sys.argv[1:]:
        raise SystemExit(diagnose())

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
    ingest_urls = _ingest_urls(os.environ.get("ACTIVITY_INGEST_URL"))
    token = os.environ.get("ACTIVITY_WRITE_TOKEN")
    if not (ingest_urls and token):
        logging.info("no ingest URL and write token, spooling locally to %s only", spool_path)
    elif len(ingest_urls) > 1:
        logging.info("ingest addresses, in order: %s", ", ".join(ingest_urls))

    adapter = create_adapter()
    buffer = InactivityBuffer(idle_threshold_seconds=IDLE_AFTER_SECONDS)

    def _shutdown_flush(*args) -> None:
        for sample in buffer.flush_all():
            spool.add(_event(machine_id, sample.app, sample.activity_state, sample.occurred_at))
        if ingest_urls and token:
            try:
                _flush(spool, ingest_urls, token, retention_days)
            except Exception:
                pass

    atexit.register(_shutdown_flush)
    try:
        signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
        signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    except (ValueError, AttributeError):
        pass

    while True:
        # A daemon that dies on a transient fault collects nothing until someone
        # notices, which is the failure the local spool exists to prevent. Every
        # step below is therefore allowed to fail without ending the loop.
        try:
            snapshot = adapter.snapshot()
            ready_samples = buffer.process(
                app=snapshot.app,
                state=snapshot.activity_state,
                idle_ms=snapshot.idle_ms,
            )
            for sample in ready_samples:
                spool.add(_event(machine_id, sample.app, sample.activity_state, sample.occurred_at))
        except Exception:
            logging.exception("snapshot failed, skipping this interval")

        if ingest_urls and token:
            try:
                accepted = _flush(spool, ingest_urls, token, retention_days)
                if accepted and _prefer(ingest_urls, accepted):
                    logging.warning("ingest failed over to %s", accepted)
            except (OSError, urllib.error.URLError, RuntimeError) as error:
                # Unsent events keep synced_at NULL, so the next pass retries them.
                logging.warning("ingest failed, %s events still spooled: %s", len(spool.pending()), error)
            except Exception:
                logging.exception("ingest failed unexpectedly, events still spooled")

        sleep_interval(interval)


if __name__ == "__main__":
    main()
