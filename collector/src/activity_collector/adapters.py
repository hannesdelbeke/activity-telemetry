"""Best-effort foreground-app and idle-state adapters."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

# Seconds without a keyboard or pointer event before the system is reported idle.
IDLE_AFTER_SECONDS = 300


@dataclass(frozen=True)
class Snapshot:
    app: str
    activity_state: str


class Adapter:
    def snapshot(self) -> Snapshot:
        raise NotImplementedError


class GenericAdapter(Adapter):
    def snapshot(self) -> Snapshot:
        return Snapshot(f"platform:{platform.system().lower()}", "active")


class LinuxAdapter(Adapter):
    def snapshot(self) -> Snapshot:
        # A locked screen is checked first, because it is the one state where
        # every other source gives a confidently wrong answer.
        if self._screen_locked():
            return Snapshot("locked", "idle")

        # On Wayland, the GNOME extension is the only source that works.
        # On X11, it is absent and the existing X11 mechanisms run instead.
        app = self._gnome_extension_app()
        if not app or app == "unknown":
            app = self._x11_app()
            if shutil.which("xdotool"):
                try:
                    window = subprocess.check_output(
                        ["xdotool", "getactivewindow", "getwindowclassname"],
                        text=True,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                    ).strip()
                    if window:
                        app = window[:128]
                except (OSError, subprocess.SubprocessError):
                    pass
        return Snapshot(app, self._activity_state())

    @staticmethod
    def _screen_locked() -> bool:
        """True when the GNOME session is locked.

        GNOME disables extensions on the lock screen, so this extension's bus
        name vanishes at exactly the moment the screen locks. The adapter then
        falls through to the X11 tiers, which on native Wayland answer
        "unknown" -- so locking the machine looked identical to a Wayland
        session where the extension had never been installed.

        The state was wrong too, and in the direction that matters. Locking
        resets the idle timer, so for the first IDLE_AFTER_SECONDS of an
        overnight lock the machine reported `unknown`/`active`: five minutes of
        apparent work, every night, at a keyboard nobody was sitting at.

        org.gnome.ScreenSaver is owned by gnome-shell itself rather than by the
        extension, so unlike the extension it survives the lock. Anything else
        -- not GNOME, no gdbus, call fails -- answers False and the ordinary
        path runs, because a wrong "locked" would erase real activity.
        """
        if not shutil.which("gdbus"):
            return False
        try:
            output = subprocess.check_output(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest=org.gnome.ScreenSaver",
                    "--object-path=/org/gnome/ScreenSaver",
                    "--method=org.gnome.ScreenSaver.GetActive",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        # The reply is the GVariant tuple '(true,)' or '(false,)', and nothing
        # else counts. A substring search for "true" would also match "untrue"
        # and any diagnostic text that happens to contain the word, and the
        # cost of a false positive here is erasing real activity.
        return re.fullmatch(r"\s*\(\s*true\s*,\s*\)\s*", output) is not None

    @staticmethod
    def _gnome_extension_app() -> str:
        """Read the focused app from the GNOME Shell extension, or unknown if unavailable."""
        if not shutil.which("gdbus"):
            return "unknown"
        try:
            output = subprocess.check_output(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest=org.activitycollector.Telemetry",
                    "--object-path=/org/activitycollector/Telemetry",
                    "--method=org.activitycollector.Telemetry.GetFocusedApp",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).strip()
            # gdbus prints the return value as a GVariant tuple, e.g. ('AppName',)
            # Extract the quoted string value.
            match = re.search(r"\('([^']*)'\s*,?\s*\)", output)
            if match:
                app = match.group(1)
                # truncate to 128 chars and never return empty string
                return app[:128] or "unknown"
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return "unknown"

    @staticmethod
    def _x11_app() -> str:
        """Read only the X11 WM_CLASS for the active window, never its title."""
        try:
            active = subprocess.check_output(
                ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            match = re.search(r"window id # (0x[0-9a-fA-F]+)", active)
            if match is None:
                return "unknown"
            properties = subprocess.check_output(
                ["xprop", "-id", match.group(1), "WM_CLASS"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        values = re.findall(r'"([^"]*)"', properties)
        return (values[-1] if values else "unknown")[:128] or "unknown"

    @staticmethod
    def _activity_state() -> str:
        # On Wayland, the GNOME extension is the only source that works.
        # On X11, it is absent and the existing mechanisms run instead.
        idle_ms = LinuxAdapter._gnome_extension_idle()
        if idle_ms is not None:
            return "idle" if idle_ms >= IDLE_AFTER_SECONDS * 1000 else "active"

        # Try X11 via XScreenSaver extension.
        idle_ms = LinuxAdapter._x11_screensaver_idle()
        if idle_ms is not None:
            return "idle" if idle_ms >= IDLE_AFTER_SECONDS * 1000 else "active"

        # Fall back to xprintidle if available.
        if shutil.which("xprintidle"):
            try:
                output = subprocess.check_output(
                    ["xprintidle"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                ).strip()
                idle_ms = int(output)
                return "idle" if idle_ms >= IDLE_AFTER_SECONDS * 1000 else "active"
            except (OSError, subprocess.SubprocessError, ValueError):
                pass

        # Best-effort Wayland via GNOME Mutter IdleMonitor.
        if shutil.which("gdbus"):
            try:
                output = subprocess.check_output(
                    [
                        "gdbus",
                        "call",
                        "--session",
                        "--dest=org.gnome.Mutter.IdleMonitor",
                        "--object-path=/org/gnome/Mutter/IdleMonitor/Core",
                        "--method=org.gnome.Mutter.IdleMonitor.GetIdletime",
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                ).strip()
                # gdbus prints the return tuple as GVariant, '(uint64 12345,)'.
                # Match just the scalar, so spacing or a future extra member in
                # the tuple does not silently turn idle detection off.
                match = re.search(r"uint64\s+(\d+)", output)
                if match:
                    idle_ms = int(match.group(1))
                    return "idle" if idle_ms >= IDLE_AFTER_SECONDS * 1000 else "active"
            except (OSError, subprocess.SubprocessError, ValueError):
                pass

        return "active"

    @staticmethod
    def _gnome_extension_idle() -> int | None:
        """Return idle milliseconds from the GNOME Shell extension, or None if unavailable."""
        if not shutil.which("gdbus"):
            return None
        try:
            output = subprocess.check_output(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest=org.activitycollector.Telemetry",
                    "--object-path=/org/activitycollector/Telemetry",
                    "--method=org.activitycollector.Telemetry.GetIdletime",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).strip()
            # gdbus prints the return tuple as GVariant, '(uint64 12345,)'.
            # Match just the scalar, tolerant of spacing.
            match = re.search(r"uint64\s+(\d+)", output)
            if match:
                idle_ms = int(match.group(1))
                # 0 means no idle information available; treat as None so we fall through
                return idle_ms if idle_ms > 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return None

    @staticmethod
    def _x11_screensaver_idle() -> int | None:
        """Return idle milliseconds from XScreenSaver extension, or None if unavailable."""
        try:
            import ctypes
            import ctypes.util

            # find_library shells out to ldconfig, gcc or objdump, none of which
            # a slim container is obliged to ship, so it returns None on machines
            # that do have the libraries. The sonames are stable, so try them
            # before giving up.
            x11_path = ctypes.util.find_library("X11") or "libX11.so.6"
            xss_path = ctypes.util.find_library("Xss") or "libXss.so.1"

            x11 = ctypes.CDLL(x11_path)
            xss = ctypes.CDLL(xss_path)

            class XScreenSaverInfo(ctypes.Structure):
                """Matches the XScreenSaverInfo in <X11/extensions/scrnsaver.h>."""

                _fields_ = [
                    ("window", ctypes.c_ulong),
                    ("state", ctypes.c_int),
                    ("kind", ctypes.c_int),
                    ("til_or_since", ctypes.c_ulong),
                    ("idle", ctypes.c_ulong),
                    ("eventMask", ctypes.c_ulong),
                ]

            # Every restype below has to be declared. ctypes assumes a function
            # returns int, so on 64-bit Linux an undeclared Display* comes back
            # truncated to its low 32 bits, and handing that back to
            # XScreenSaverQueryInfo dereferences a corrupt pointer. That is a
            # SIGSEGV, which no `except` here can catch: it would take the whole
            # collector down every poll, and the service manager would restart it
            # into the same crash.
            info_p = ctypes.POINTER(XScreenSaverInfo)
            int_p = ctypes.POINTER(ctypes.c_int)
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            x11.XFree.argtypes = [ctypes.c_void_p]
            xss.XScreenSaverQueryExtension.restype = ctypes.c_int
            xss.XScreenSaverQueryExtension.argtypes = [ctypes.c_void_p, int_p, int_p]
            xss.XScreenSaverAllocInfo.restype = info_p
            xss.XScreenSaverQueryInfo.restype = ctypes.c_int
            xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, info_p]

            display = x11.XOpenDisplay(None)
            if not display:
                return None

            try:
                # A display can exist without the extension compiled into the
                # server. Asking first is what separates "X11 cannot answer this"
                # from "the user is at the keyboard", so the caller can fall
                # through to xprintidle or gdbus instead of trusting a stale zero.
                event_base, error_base = ctypes.c_int(), ctypes.c_int()
                if not xss.XScreenSaverQueryExtension(
                    display, ctypes.byref(event_base), ctypes.byref(error_base)
                ):
                    return None

                info = xss.XScreenSaverAllocInfo()
                if not info:
                    return None
                try:
                    # QueryInfo leaves the struct untouched when it fails, so
                    # without this check a failure reads back as zero idle
                    # milliseconds, which is indistinguishable from real activity.
                    if not xss.XScreenSaverQueryInfo(
                        display, x11.XDefaultRootWindow(display), info
                    ):
                        return None
                    return int(info.contents.idle)
                finally:
                    x11.XFree(info)
            finally:
                x11.XCloseDisplay(display)
        except (OSError, AttributeError, ValueError):
            return None


class WindowsAdapter(Adapter):
    def snapshot(self) -> Snapshot:
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            # Deliberately return no title; only a generic active marker is kept.
            return Snapshot("windows-foreground" if length >= 0 else "unknown", "active")
        except (AttributeError, OSError):
            return Snapshot("windows", "active")


class MacAdapter(Adapter):
    def snapshot(self) -> Snapshot:
        return Snapshot(self._front_app(), self._activity_state())

    @staticmethod
    def _front_app() -> str:
        # lsappinfo needs no Accessibility grant, where reading the frontmost
        # process through System Events does. It names the application only,
        # never the window title.
        try:
            asn = subprocess.check_output(
                ["lsappinfo", "front"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).strip()
            if not asn:
                return "unknown"
            info = subprocess.check_output(
                ["lsappinfo", "info", "-only", "name", asn],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        # The answer is '"LSDisplayName"="Terminal"'; keep the value.
        _, _, name = info.partition("=")
        return name.strip().strip('"')[:128] or "unknown"

    @staticmethod
    def _activity_state() -> str:
        try:
            registry = subprocess.check_output(
                ["ioreg", "-n", "IOHIDSystem", "-r", "-d", "1"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return "active"
        match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', registry)
        if match is None:
            return "active"
        # HIDIdleTime counts nanoseconds since the last keyboard or pointer event.
        idle_seconds = int(match.group(1)) / 1_000_000_000
        return "idle" if idle_seconds >= IDLE_AFTER_SECONDS else "active"


def create_adapter() -> Adapter:
    system = platform.system()
    if system == "Linux":
        return LinuxAdapter()
    if system == "Windows":
        return WindowsAdapter()
    if system == "Darwin":
        return MacAdapter()
    return GenericAdapter()


def sleep_interval(seconds: int) -> None:
    time.sleep(seconds)
