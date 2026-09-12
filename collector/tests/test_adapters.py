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
    import ctypes
    import ctypes.util

    # find_library missing no longer ends it, the sonames are tried next, so the
    # None has to come from those failing to load rather than from the lookup.
    monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)
    monkeypatch.setattr(ctypes, "CDLL", _raise_oserror)
    assert adapters.LinuxAdapter._x11_screensaver_idle() is None


def _raise_oserror(*args, **kwargs):
    raise OSError("no such library")


class _FakeFunc:
    """One C function, carrying the restype/argtypes the adapter declares on it."""

    def __init__(self, impl):
        self._impl = impl
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self._impl(self, *args)


class _FakeLib:
    def __init__(self, impls):
        self._impls = impls
        self._funcs = {}

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._impls:
            raise AttributeError(name)
        return self._funcs.setdefault(name, _FakeFunc(self._impls[name]))


def _fake_x11(monkeypatch, *, idle_ms=1000, has_extension=True, query_ok=True):
    """Stand in for libX11 and libXss, following the same contract the real ones do.

    AllocInfo hands back a zeroed struct and only QueryInfo fills `idle`, which is
    what makes the "query failed" case distinguishable from "zero idle".
    """
    import ctypes
    import ctypes.util

    state = {"freed": [], "closed": [], "structs": []}

    def open_display(func, name):
        return 0x5EED

    def alloc_info(func):
        struct = func.restype._type_()  # POINTER(XScreenSaverInfo) -> the class
        state["structs"].append(struct)  # keep it alive past the call
        return ctypes.pointer(struct)

    def query_info(func, display, window, info):
        if not query_ok:
            return 0
        info.contents.idle = idle_ms
        return 1

    x11 = _FakeLib(
        {
            "XOpenDisplay": open_display,
            "XCloseDisplay": lambda func, d: state["closed"].append(d),
            "XDefaultRootWindow": lambda func, d: 0x2A,
            "XFree": lambda func, p: state["freed"].append(p),
        }
    )
    xss = _FakeLib(
        {
            "XScreenSaverQueryExtension": lambda func, d, e, r: int(has_extension),
            "XScreenSaverAllocInfo": alloc_info,
            "XScreenSaverQueryInfo": query_info,
        }
    )

    monkeypatch.setattr(ctypes.util, "find_library", lambda name: f"lib{name}")
    monkeypatch.setattr(ctypes, "CDLL", lambda path: x11 if path == "libX11" else xss)
    state["x11"], state["xss"] = x11, xss
    return state


def test_linux_x11_screensaver_idle_reads_the_idle_field(monkeypatch):
    state = _fake_x11(monkeypatch, idle_ms=42_000)
    assert adapters.LinuxAdapter._x11_screensaver_idle() == 42_000
    # the display and the struct are C allocations, and this runs every poll
    assert state["closed"] == [0x5EED]
    assert len(state["freed"]) == 1


def test_linux_x11_screensaver_idle_is_none_without_the_extension(monkeypatch):
    state = _fake_x11(monkeypatch, has_extension=False)
    assert adapters.LinuxAdapter._x11_screensaver_idle() is None
    assert state["closed"] == [0x5EED]  # still closed on the early return


def test_linux_x11_screensaver_idle_is_none_when_the_query_fails(monkeypatch):
    # The struct is left zeroed when QueryInfo fails. Returning that as 0 would
    # read as "the user just touched the keyboard" and pin the machine active.
    _fake_x11(monkeypatch, query_ok=False)
    assert adapters.LinuxAdapter._x11_screensaver_idle() is None


def test_linux_x11_screensaver_declares_its_pointer_types(monkeypatch):
    # ctypes defaults every restype to int, which truncates a 64-bit pointer and
    # segfaults the collector rather than raising something catchable.
    import ctypes

    state = _fake_x11(monkeypatch)
    adapters.LinuxAdapter._x11_screensaver_idle()
    assert state["x11"].XOpenDisplay.restype is ctypes.c_void_p
    assert state["x11"].XDefaultRootWindow.restype is ctypes.c_ulong
    assert state["xss"].XScreenSaverAllocInfo.restype is not ctypes.c_int
    assert state["xss"].XScreenSaverAllocInfo.restype._type_ is state["structs"][0].__class__


def test_linux_x11_screensaver_info_matches_the_c_abi(monkeypatch):
    """The struct is read straight out of C memory, so a wrong layout reads junk."""
    import ctypes

    state = _fake_x11(monkeypatch)
    adapters.LinuxAdapter._x11_screensaver_idle()
    info = state["structs"][0].__class__

    # transcribed independently from <X11/extensions/scrnsaver.h>
    class Reference(ctypes.Structure):
        _fields_ = [
            ("window", ctypes.c_ulong),
            ("state", ctypes.c_int),
            ("kind", ctypes.c_int),
            ("til_or_since", ctypes.c_ulong),
            ("idle", ctypes.c_ulong),
            ("eventMask", ctypes.c_ulong),
        ]

    assert ctypes.sizeof(info) == ctypes.sizeof(Reference)
    for name, _ in Reference._fields_:
        assert getattr(info, name).offset == getattr(Reference, name).offset, name
        assert getattr(info, name).size == getattr(Reference, name).size, name
    if ctypes.sizeof(ctypes.c_ulong) == 8:  # the LP64 numbers, spelled out
        assert (info.idle.offset, ctypes.sizeof(info)) == (24, 40)


def test_linux_gdbus_idle_tolerates_the_variant_spacing(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: None)
    monkeypatch.setattr(adapters.shutil, "which", lambda name: name == "gdbus" or None)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "(uint64  400000,)\n")
    assert adapters.LinuxAdapter._activity_state() == "idle"


def test_linux_selects_the_linux_adapter(monkeypatch):
    monkeypatch.setattr(adapters.platform, "system", lambda: "Linux")
    assert isinstance(adapters.create_adapter(), adapters.LinuxAdapter)
