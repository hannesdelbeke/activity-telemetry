# Activity Collector

Privacy-preserving local activity collector for personal computers.

The collector records coarse active/idle state and allowlisted foreground
application names. It never records keystrokes, window titles, document names,
URLs, screen contents, notifications, or message contents.

## Design

- One Python codebase for Linux and Windows.
- OS-specific adapters live behind the same collector interface.
- SQLite local spool means collection continues while offline.
- Events are sent in bounded batches to an append-only HTTPS endpoint.
- No server read credentials are stored on the client.

Separate Windows and Linux repositories are not needed: the protocol, spool,
configuration, and privacy behavior are shared. Only idle-time and foreground
application detection differ.

## Status

The repository contains the protocol and spool foundation plus adapter
interfaces. OS adapters should be enabled only after reviewing the platform
permissions and privacy settings for the intended machine.

## Configuration

Environment variables:

| Variable | Required | Description |
| --- | --- | --- |
| `ACTIVITY_MACHINE_ID` | yes | Non-identifying device label |
| `ACTIVITY_SPOOL` | no | SQLite path; defaults to a user cache path |
| `ACTIVITY_INGEST_URL` | no | HTTPS batch endpoint |
| `ACTIVITY_WRITE_TOKEN` | no | Device-scoped write token |
| `ACTIVITY_INTERVAL_SECONDS` | no | Poll interval; defaults to 30 |

Do not commit tokens or machine-specific configuration.

## Run

```bash
python -m activity_collector
```

The default collector is intentionally dry-run/local-only until an ingest URL
and write token are configured.

## Event contract

```json
{
  "event_id": "stable-client-generated-id",
  "schema_version": 1,
  "occurred_at": "2026-01-01T12:00:00Z",
  "machine_id": "personal-linux",
  "event_type": "app_focus",
  "data": {
    "app": "example",
    "activity_state": "active"
  }
}
```

Use generic machine labels. Do not include employer names, personal names,
paths, account identifiers, or health data.
