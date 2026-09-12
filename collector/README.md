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

Linux, macOS, and Windows adapters exist, along with the spool, the batch
protocol, and a startup service for each platform. Only the macOS adapter
reports a real `activity_state`; Linux and Windows still report `active`
unconditionally, so that field is not yet comparable across machines.

Review the platform permissions and privacy settings for a machine before
enabling collection on it.

## Configuration

Environment variables:

| Variable | Required | Description |
| --- | --- | --- |
| `ACTIVITY_MACHINE_ID` | yes | Non-identifying device label |
| `ACTIVITY_SPOOL` | no | SQLite path; defaults to a user cache path |
| `ACTIVITY_INGEST_URL` | no | Batch ingest endpoint; HTTPS outside a trusted LAN |
| `ACTIVITY_WRITE_TOKEN` | no | Device-scoped write token |
| `ACTIVITY_INTERVAL_SECONDS` | no | Poll interval; defaults to 30 |
| `ACTIVITY_SPOOL_RETENTION_DAYS` | no | Days to keep events the sink has confirmed; defaults to 7 |

Do not commit tokens or machine-specific configuration.

The collector never deletes an event the sink has not acknowledged. An ingest
failure is logged and retried on the next interval, so a sink that is down or
unreachable costs latency, not data.

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

## Install as a startup service

Each installer generates its own unit against wherever the repository actually
sits, creates the config file, and starts the collector at login. All three
leave the collector spooling locally and uploading nothing until
`ACTIVITY_INGEST_URL` and `ACTIVITY_WRITE_TOKEN` are filled in.

### macOS (launchd)

```sh
sh collector/macos/install.sh
$EDITOR ~/.config/activity-collector/env     # set the ingest URL and token
launchctl kickstart -k gui/$(id -u)/com.activity-collector
```

Logs go to `~/Library/Logs/activity-collector.log`. The agent has `KeepAlive`
set, so it restarts if the collector exits.

### Linux (systemd user service)

```sh
sh collector/linux/install.sh
$EDITOR ~/.config/activity-collector/env
systemctl --user restart activity-collector
```

Logs go to `journalctl --user -u activity-collector -f`. The unit committed at
`systemd/activity-collector.service` is a reference copy with a hardcoded
checkout path; the installer generates the one that actually gets used.

### Windows (Task Scheduler)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File collector\windows\install.ps1 `
  -MachineId personal-windows -IngestUrl http://homeassistant.local:8788/api/ingest -WriteToken <token>
Start-ScheduledTask -TaskName 'Activity Collector'
```

It runs under `pythonw.exe` so no console window opens at logon, and it refuses
the Microsoft Store python, whose execution alias the scheduler cannot resolve.

## Tests

```sh
python -m pytest collector/tests haos-addon/tests
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
