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
