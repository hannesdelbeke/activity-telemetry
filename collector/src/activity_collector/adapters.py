"""Best-effort foreground-app and idle-state adapters."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

# Seconds without a keyboard or pointer event before macOS is reported idle.
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
        return Snapshot(app, "active")

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
