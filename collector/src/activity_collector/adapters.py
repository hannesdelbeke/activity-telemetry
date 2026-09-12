"""Best-effort foreground-app and idle-state adapters."""

from __future__ import annotations

import platform
import shutil
import subprocess
import time
from dataclasses import dataclass


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
        app = "unknown"
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


def create_adapter() -> Adapter:
    system = platform.system()
    if system == "Linux":
        return LinuxAdapter()
    if system == "Windows":
        return WindowsAdapter()
    return GenericAdapter()


def sleep_interval(seconds: int) -> None:
    time.sleep(seconds)
