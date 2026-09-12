# Linux-specific components

## GNOME Shell extension for Wayland

On native Wayland, ordinary clients cannot ask which window is focused — the compositor keeps that private. Without this extension, the collector reports `"unknown"` for all native Wayland windows.

The GNOME Shell extension in `gnome-extension/` runs inside the compositor and exposes the focused app name and idle time over D-Bus. The collector's Linux adapter tries the extension first, then falls back to X11 mechanisms when absent.

### Requirements

GNOME Shell 45, 46, 47, or 48. Earlier versions used a different extension API and are not supported.

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

Should return the current app name, not an error.

### What it reads

The extension calls `Shell.WindowTracker.get_default().focus_app` to get the application, then returns `get_id()` or `get_name()`. It never calls `get_title()` on a window. This matches the collector's privacy contract: app identifier only, never window titles or document names.

Idle time comes from `global.backend.get_core_idle_monitor().get_idletime()`.
