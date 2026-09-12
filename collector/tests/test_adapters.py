"""macOS adapter parsing, without shelling out to the real commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from activity_collector import adapters  # noqa: E402


def test_front_app_reads_the_name_out_of_lsappinfo(monkeypatch):
    outputs = iter(["ASN:0x0-0x1e01e:", '"LSDisplayName"="Terminal"'])
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: next(outputs))
    assert adapters.MacAdapter._front_app() == "Terminal"


def test_front_app_falls_back_when_there_is_no_frontmost_app(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "")
    assert adapters.MacAdapter._front_app() == "unknown"


def test_front_app_survives_a_missing_lsappinfo(monkeypatch):
    def explode(*_args, **_kwargs):
        raise FileNotFoundError("lsappinfo")

    monkeypatch.setattr(subprocess, "check_output", explode)
    assert adapters.MacAdapter._front_app() == "unknown"


@pytest.mark.parametrize(
    "idle_nanoseconds, expected",
    [(0, "active"), (299 * 1_000_000_000, "active"), (301 * 1_000_000_000, "idle")],
)
def test_activity_state_thresholds_on_hid_idle_time(monkeypatch, idle_nanoseconds, expected):
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *a, **k: f'  | |   "HIDIdleTime" = {idle_nanoseconds}\n',
    )
    assert adapters.MacAdapter._activity_state() == expected


def test_activity_state_defaults_to_active_when_ioreg_says_nothing(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "no idle key here")
    assert adapters.MacAdapter._activity_state() == "active"


def test_darwin_selects_the_mac_adapter(monkeypatch):
    monkeypatch.setattr(adapters.platform, "system", lambda: "Darwin")
    assert isinstance(adapters.create_adapter(), adapters.MacAdapter)


def test_linux_activity_state_uses_x11_screensaver_when_available(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: 100_000)
    assert adapters.LinuxAdapter._activity_state() == "active"

    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: 301_000)
    assert adapters.LinuxAdapter._activity_state() == "idle"


def test_linux_activity_state_falls_back_to_xprintidle(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: None)
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "xprintidle" and "/usr/bin/xprintidle")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "100000")
    assert adapters.LinuxAdapter._activity_state() == "active"

    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "301000")
    assert adapters.LinuxAdapter._activity_state() == "idle"


def test_linux_activity_state_falls_back_to_gdbus_for_wayland(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: None)
    which_results = {"gdbus": "/usr/bin/gdbus"}
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: which_results.get(cmd))
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "(uint64 100000,)")
    assert adapters.LinuxAdapter._activity_state() == "active"

    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "(uint64 301000,)")
    assert adapters.LinuxAdapter._activity_state() == "idle"


def test_linux_activity_state_defaults_to_active_when_no_mechanism_available(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: None)
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: None)
    assert adapters.LinuxAdapter._activity_state() == "active"


def test_linux_activity_state_survives_xprintidle_failure(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: None)
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "xprintidle" and "/usr/bin/xprintidle")

    def explode(*_args, **_kwargs):
        raise FileNotFoundError("xprintidle")

    monkeypatch.setattr(subprocess, "check_output", explode)
    assert adapters.LinuxAdapter._activity_state() == "active"


def test_linux_activity_state_survives_gdbus_failure(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: None)
    which_results = {"gdbus": "/usr/bin/gdbus"}
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: which_results.get(cmd))

    def explode(*_args, **_kwargs):
        raise subprocess.SubprocessError("gdbus failed")

    monkeypatch.setattr(subprocess, "check_output", explode)
    assert adapters.LinuxAdapter._activity_state() == "active"


def test_linux_x11_screensaver_idle_returns_none_when_libraries_missing(monkeypatch):
    import ctypes.util

    monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)
    assert adapters.LinuxAdapter._x11_screensaver_idle() is None


def test_linux_selects_the_linux_adapter(monkeypatch):
    monkeypatch.setattr(adapters.platform, "system", lambda: "Linux")
    assert isinstance(adapters.create_adapter(), adapters.LinuxAdapter)
