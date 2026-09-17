# Linux-specific components

## GNOME Shell extension for Wayland

On native Wayland, ordinary clients cannot ask which window is focused — the compositor keeps that private. Without this extension, the collector reports `"unknown"` for all native Wayland windows.

The GNOME Shell extension in `gnome-extension/` runs inside the compositor and exposes the focused app name and idle time over D-Bus. The collector's Linux adapter tries the extension first, then falls back to X11 mechanisms when absent.

### Requirements

GNOME Shell 45 or newer, up to the versions listed in `gnome-extension/metadata.json`.
Earlier versions used `imports.gi` and the pre-ESM extension API, and are not
supported.

### Install

```bash
sh collector/linux/install-gnome-extension.sh
```

Then log out and log back in (Wayland requires this; X11 can use Alt+F2 → `r`).

### Verify

```bash
gdbus call --session \
  --dest=org.activitycollector.Telemetry \
  --object-path=/org/activitycollector/Telemetry \
  --method=org.activitycollector.Telemetry.GetFocusedApp
```

Should return the current app name, not an error. `GetIdletime` is the other
method, and returns `(uint64 <milliseconds>,)`.

An error here and a working collector are easy to confuse, because the adapter
treats "the extension is not there" and "the extension returned nothing" the
same way and falls back silently. So check what the adapter actually sees,
rather than only that the bus call works:

```sh
PYTHONPATH=collector/src python -c \
  'from activity_collector.adapters import LinuxAdapter; print(LinuxAdapter().snapshot())'
```

On Wayland without the extension that prints `app='unknown'`. With it, the real
application. Move the mouse and it reports `active`; leave the machine alone for
longer than `IDLE_AFTER_SECONDS` in `adapters.py`, 120 (2 minutes) by default, and it reports
`idle`.

End to end, once the collector is running, the spool is at
`~/.cache/activity-collector/events.db` unless `ACTIVITY_SPOOL` says otherwise.
The app and state live inside `payload_json` rather than in columns of their own:

```sh
sqlite3 ~/.cache/activity-collector/events.db "
SELECT occurred_at,
       json_extract(payload_json,'\$.data.app') AS app,
       json_extract(payload_json,'\$.data.activity_state') AS state
FROM events ORDER BY occurred_at DESC LIMIT 10;"
```

`occurred_at` is UTC, which is worth remembering before concluding the collector
has stalled. A row with `synced_at` still null has not been accepted by the sink
yet; the collector never deletes an event the sink has not confirmed.

### What it reads

The extension calls `Shell.WindowTracker.get_default().focus_app` to get the application, then returns `get_id()` or `get_name()`. It never calls `get_title()` on a window. This matches the collector's privacy contract: app identifier only, never window titles or document names.

Idle time comes from `global.backend.get_core_idle_monitor().get_idletime()`.
