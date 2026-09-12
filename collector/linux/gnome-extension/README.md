# GNOME Shell Extension for Activity Collector

Privacy-preserving D-Bus service that exposes the focused application name and
idle time for the activity collector. Never reads window titles.

## What it exposes

D-Bus service `org.activitycollector.Telemetry` at path `/org/activitycollector/Telemetry`:

- `GetFocusedApp() -> s` — returns the application id or name of the focused window, or `"unknown"` when nothing is focused. Capped at 128 characters. Never reads window titles.
- `GetIdletime() -> t` — returns idle time in milliseconds since the last keyboard or pointer event. Returns `0` when idle information is unavailable.

## Requirements

GNOME Shell 45 or later. Earlier versions used a different extension API
(`imports.gi` instead of ESM) and are not supported.

## Install

Run the installer from the repository root:

```bash
sh collector/linux/install-gnome-extension.sh
```

Then log out and log back in on Wayland (the shell cannot be restarted in
place). On X11 you can restart the shell with Alt+F2, type `r`, and press Enter.

## Why this exists

On native Wayland, ordinary clients cannot ask which window is focused — the
compositor keeps that information private. A GNOME Shell extension runs inside
the compositor and can access it. The extension is the only mechanism that lets
the collector report a real application name on Wayland instead of `"unknown"`.

On X11 and XWayland, the collector already has working mechanisms and the
extension is not needed, but it works there too.
