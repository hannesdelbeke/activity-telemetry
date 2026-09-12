# Activity Collector

Privacy-preserving local activity collector for personal computers.

The collector records coarse active/idle state and allowlisted foreground
application names. It never records keystrokes, window titles, document names,
URLs, screen contents, notifications, or message contents.

## Design

- One Python codebase for Linux, macOS, and Windows.
- OS-specific adapters live behind the same collector interface.
- SQLite local spool means collection continues while offline.
- Events are sent in bounded batches to an append-only HTTPS endpoint.
- No server read credentials are stored on the client.

Separate per-OS repositories are not needed: the protocol, spool,
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
| `ACTIVITY_INGEST_URL` | no | Batch ingest endpoint; HTTPS outside a trusted LAN |
| `ACTIVITY_WRITE_TOKEN` | no | Device-scoped write token |
| `ACTIVITY_INTERVAL_SECONDS` | no | Poll interval; defaults to 30 |

Do not commit tokens or machine-specific configuration.

## Run

```bash
python -m activity_collector
```

The default collector is intentionally dry-run/local-only until an ingest URL
and write token are configured.

### macOS

The macOS adapter reads the foreground application name with `lsappinfo` and
idle time from the `IOHIDSystem` registry entry with `ioreg`. Both ship with
the OS, and neither needs an Accessibility or Screen Recording grant — reading
the frontmost process through System Events would, which is why `lsappinfo` is
used instead. Only the application name is read, never the window title.

Idle is reported after `IDLE_AFTER_SECONDS` in `adapters.py`, 300 by default.
This is currently the only adapter that reports a real `activity_state`; Linux
and Windows report `active` unconditionally.

### Linux user service

Copy `systemd/activity-collector.service` to
`~/.config/systemd/user/`, create `~/.config/activity-collector/env` with a
generic `ACTIVITY_MACHINE_ID`, then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now activity-collector.service
```

The service starts when the graphical user session starts and writes to the
local SQLite spool. It does not upload anything unless both
`ACTIVITY_INGEST_URL` and `ACTIVITY_WRITE_TOKEN` are configured.

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
