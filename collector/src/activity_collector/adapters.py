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
        # Try X11 via XScreenSaver extension first.
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
                # Output is '(uint64 12345,)' — extract the number.
                match = re.search(r"\(uint64 (\d+),\)", output)
                if match:
                    idle_ms = int(match.group(1))
                    return "idle" if idle_ms >= IDLE_AFTER_SECONDS * 1000 else "active"
            except (OSError, subprocess.SubprocessError, ValueError):
                pass

        return "active"

    @staticmethod
    def _x11_screensaver_idle() -> int | None:
        """Return idle milliseconds from XScreenSaver extension, or None if unavailable."""
        try:
            import ctypes
            import ctypes.util

            # Load X11 libraries.
            x11_path = ctypes.util.find_library("X11")
            xss_path = ctypes.util.find_library("Xss")
            if not x11_path or not xss_path:
                return None

            x11 = ctypes.CDLL(x11_path)
            xss = ctypes.CDLL(xss_path)

            class XScreenSaverInfo(ctypes.Structure):
                _fields_ = [
                    ("window", ctypes.c_ulong),
                    ("state", ctypes.c_int),
                    ("kind", ctypes.c_int),
                    ("til_or_since", ctypes.c_ulong),
                    ("idle", ctypes.c_ulong),
                    ("eventMask", ctypes.c_ulong),
                ]

            # Open the default display.
            display = x11.XOpenDisplay(None)
            if not display:
                return None

            try:
                # Allocate info structure.
                info = xss.XScreenSaverAllocInfo()
                if not info:
                    return None

                try:
                    # Query idle time.
                    xss.XScreenSaverQueryInfo(display, x11.XDefaultRootWindow(display), info)
                    idle_ms = ctypes.cast(info, ctypes.POINTER(XScreenSaverInfo)).contents.idle
                    return int(idle_ms)
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
