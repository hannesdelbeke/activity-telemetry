# GNOME focused-app integration

## Problem to solve

The Linux collector currently reports `app: "unknown"` on this machine because
the desktop session is GNOME on native Wayland. `xprop`, `xdotool`, and other
X11 tools can see only the Xwayland placeholder, not the focused native
Wayland window. Wayland intentionally does not provide a universal
foreground-window API to ordinary applications.

This makes the Linux activity data much less useful for comparing computer
activity with health, exercise, sleep, and daily notes. The collector needs a
coarse answer to “which application is currently focused?”—for example,
`org.mozilla.firefox` or `org.gnome.Terminal`—without collecting private
content.

## Proposed solution

Add a small GNOME Shell extension that runs in the user's GNOME desktop
session. It should:

- observe the focused Shell window;
- read only a stable application identity, such as desktop app ID, WM class,
  or application name;
- expose that value only to a local collector process;
- return `unknown` when no safe application identity is available;
- avoid window titles, document names, URLs, keystrokes, notifications, screen
  contents, accessibility data, and message contents.

The extension does not modify or inject into applications. It observes the
desktop Shell's focus state, so it can cover both native Wayland and Xwayland
applications in the current graphical session. It does not affect background
services, other users' sessions, or applications themselves.

The extension is a privileged desktop-session component, so it must remain
small, open source, local-only, and limited to an allowlisted response. It
should not accept arbitrary commands or expose a network listener.

## Where it should live

Keep this in the existing public `activity-telemetry` repository:

```text
activity-telemetry/
  collector/
    src/activity_collector/
      adapters.py
      gnome_focus.py       # Python client for the local integration
  gnome-shell-extension/  # GNOME-side focus adapter
    extension.js
    metadata.json
    README.md
  docs/
    gnome-focused-app-integration.md
```

The GNOME extension is a Linux collector support component, not a separate
repository. Keeping it next to the Python adapter makes the local protocol,
privacy rules, versioning, and installation instructions reviewable together.
It should not be part of the HAOS add-on: HAOS receives already-sanitized
events and has no desktop session.

## Local interface

Prefer a session-local D-Bus interface, or another authenticated
session-local IPC mechanism already supported by GNOME. The interface should
have one read operation, conceptually:

```text
GetFocusedApp() -> app_id
```

The extension must restrict access to the local user/session and return only a
sanitized identifier. Do not use a TCP/UDP listener, Unix socket with broad
permissions, or a shell command that returns window titles.

The Python Linux adapter should:

1. try the GNOME integration first when the session is GNOME on Wayland;
2. validate and normalize the returned identifier to a short safe app label;
3. fall back to the existing X11/Xwayland `WM_CLASS` lookup;
4. return `unknown` only when neither source is available.

The collector's event schema does not need to change. The result remains
`data.app`, and the existing SQLite spool and HAOS ingest path continue to
work unchanged.

## How Linux should run it

The extension should be installed for the current user, not system-wide. The
Linux installer should:

1. install or enable the extension in the user's GNOME extensions directory;
2. enable it through the supported GNOME Extensions mechanism;
3. verify that the local IPC endpoint is available;
4. install or restart the existing `activity-collector.service`;
5. leave collection local-only until the user configures the ingest URL and
   write token.

The extension should start and stop with the user's GNOME session. The
collector remains a separate systemd user service and polls the integration at
the normal collection interval. If the extension is disabled, crashes, or the
session is not GNOME, the collector must keep running and use the existing
fallback behavior.

Installation must not change all applications, install application hooks, or
request Screen Recording, Accessibility, or root privileges. It only adds a
desktop-session focus observer for the logged-in user.

## Implementation and verification plan

Claude should implement this in a separate change, with:

- GNOME Shell extension metadata compatible with the supported GNOME version;
- a minimal local IPC API and explicit access restrictions;
- Python adapter tests for valid, malformed, unavailable, and fallback results;
- extension-side tests or a small manual verification procedure;
- installer/uninstaller behavior that does not overwrite the collector's
  existing environment file or SQLite spool;
- documentation of supported GNOME versions and known Wayland limitations.

Verify that events contain only app identifiers, that `unknown` remains a safe
fallback, and that existing local events are never deleted during installation
or upgrade.
