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


def test_linux_activity_state_is_unknown_when_no_mechanism_available(monkeypatch):
    # Not "active". Walking away from a machine whose probes all decline is the
    # exact case this reports, and calling it active fabricates a working day.
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: None)
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: None)
    assert adapters.LinuxAdapter._activity_state() == "unknown"


def test_linux_activity_state_survives_xprintidle_failure(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: None)
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "xprintidle" and "/usr/bin/xprintidle")

    def explode(*_args, **_kwargs):
        raise FileNotFoundError("xprintidle")

    monkeypatch.setattr(subprocess, "check_output", explode)
    assert adapters.LinuxAdapter._activity_state() == "unknown"


def test_linux_activity_state_survives_gdbus_failure(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: None)
    which_results = {"gdbus": "/usr/bin/gdbus"}
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: which_results.get(cmd))

    def explode(*_args, **_kwargs):
        raise subprocess.SubprocessError("gdbus failed")

    monkeypatch.setattr(subprocess, "check_output", explode)
    assert adapters.LinuxAdapter._activity_state() == "unknown"


def test_linux_a_zero_idle_reading_means_active_not_unknown(monkeypatch):
    # The instant after a keypress the monitor reports 0ms idle. That has to
    # reach the threshold comparison, not be mistaken for a probe declining --
    # otherwise the one moment you are provably at the keyboard reads as unknown.
    monkeypatch.setattr(adapters.LinuxAdapter, "_gnome_extension_idle", lambda: 0)
    assert adapters.LinuxAdapter._activity_state() == "active"


def test_linux_idle_reading_names_the_source_that_answered(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_gnome_extension_idle", lambda: None)
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: None)
    monkeypatch.setattr(adapters.LinuxAdapter, "_xprintidle_idle", lambda: 7_000)
    monkeypatch.setattr(adapters.LinuxAdapter, "_mutter_idle", lambda: 9_999)
    assert adapters.LinuxAdapter.idle_reading() == ("xprintidle", 7_000)


def test_linux_idle_reading_says_none_when_every_probe_declines(monkeypatch):
    for probe in ("_gnome_extension_idle", "_x11_screensaver_idle", "_xprintidle_idle", "_mutter_idle"):
        monkeypatch.setattr(adapters.LinuxAdapter, probe, lambda: None)
    assert adapters.LinuxAdapter.idle_reading() == ("none", None)


def test_linux_idle_reading_prefers_the_extension_over_every_later_probe(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_gnome_extension_idle", lambda: 1_000)
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: 2_000)
    monkeypatch.setattr(adapters.LinuxAdapter, "_xprintidle_idle", lambda: 3_000)
    monkeypatch.setattr(adapters.LinuxAdapter, "_mutter_idle", lambda: 4_000)
    assert adapters.LinuxAdapter.idle_reading() == ("gnome-extension", 1_000)


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


def test_linux_gnome_extension_supplies_app_name(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "gdbus" and "/usr/bin/gdbus")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "('org.gnome.Terminal',)\n")
    assert adapters.LinuxAdapter._gnome_extension_app() == "org.gnome.Terminal"


def test_linux_gnome_extension_app_falls_back_on_unknown(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "gdbus" and "/usr/bin/gdbus")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "('unknown',)\n")
    assert adapters.LinuxAdapter._gnome_extension_app() == "unknown"


def test_linux_gnome_extension_app_handles_empty_reply(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "gdbus" and "/usr/bin/gdbus")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "('',)\n")
    # empty string should be converted to "unknown"
    assert adapters.LinuxAdapter._gnome_extension_app() == "unknown"


def test_linux_gnome_extension_app_handles_malformed_reply(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "gdbus" and "/usr/bin/gdbus")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "malformed output")
    assert adapters.LinuxAdapter._gnome_extension_app() == "unknown"


def test_linux_gnome_extension_app_returns_unknown_without_gdbus(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: None)
    assert adapters.LinuxAdapter._gnome_extension_app() == "unknown"


def test_linux_gnome_extension_app_survives_subprocess_failure(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "gdbus" and "/usr/bin/gdbus")

    def explode(*_args, **_kwargs):
        raise subprocess.SubprocessError("gdbus failed")

    monkeypatch.setattr(subprocess, "check_output", explode)
    assert adapters.LinuxAdapter._gnome_extension_app() == "unknown"


def test_linux_gnome_extension_idle_returns_milliseconds(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "gdbus" and "/usr/bin/gdbus")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "(uint64 42000,)\n")
    assert adapters.LinuxAdapter._gnome_extension_idle() == 42000


def test_linux_gnome_extension_idle_takes_zero_at_face_value(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "gdbus" and "/usr/bin/gdbus")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "(uint64 0,)\n")
    # 0 is what the monitor reports the instant after a keypress. The extension
    # signals "cannot tell" by raising, which gdbus turns into a non-zero exit,
    # so this path no longer has to overload 0 to mean both.
    assert adapters.LinuxAdapter._gnome_extension_idle() == 0


def test_linux_gnome_extension_idle_is_none_when_the_extension_raises(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "gdbus" and "/usr/bin/gdbus")

    def gdbus_error(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "gdbus", stderr="GDBus.Error:...: no core idle monitor")

    monkeypatch.setattr(subprocess, "check_output", gdbus_error)
    assert adapters.LinuxAdapter._gnome_extension_idle() is None


def test_linux_gnome_extension_idle_tolerates_variant_spacing(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "gdbus" and "/usr/bin/gdbus")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "(uint64  42000,)\n")
    assert adapters.LinuxAdapter._gnome_extension_idle() == 42000


def test_linux_gnome_extension_idle_returns_none_without_gdbus(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: None)
    assert adapters.LinuxAdapter._gnome_extension_idle() is None


def test_linux_gnome_extension_idle_survives_subprocess_failure(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "gdbus" and "/usr/bin/gdbus")

    def explode(*_args, **_kwargs):
        raise subprocess.SubprocessError("gdbus failed")

    monkeypatch.setattr(subprocess, "check_output", explode)
    assert adapters.LinuxAdapter._gnome_extension_idle() is None


def test_linux_gnome_extension_idle_handles_malformed_reply(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: cmd == "gdbus" and "/usr/bin/gdbus")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "malformed output")
    assert adapters.LinuxAdapter._gnome_extension_idle() is None


def test_linux_snapshot_prefers_gnome_extension_app_over_x11(monkeypatch):
    # When the extension is available, it should be used and _x11_app not called
    called_x11 = []

    def mock_gnome_ext():
        return "org.gnome.Nautilus"

    def mock_x11():
        called_x11.append(True)
        return "should-not-see-this"

    def mock_activity():
        return "active"

    monkeypatch.setattr(adapters.LinuxAdapter, "_gnome_extension_app", staticmethod(mock_gnome_ext))
    monkeypatch.setattr(adapters.LinuxAdapter, "_activity_state", staticmethod(mock_activity))
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_app", staticmethod(mock_x11))
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: None)

    snapshot = adapters.LinuxAdapter().snapshot()
    assert snapshot.app == "org.gnome.Nautilus"
    assert len(called_x11) == 0  # _x11_app should not have been called


def test_linux_snapshot_falls_back_to_x11_when_extension_unavailable(monkeypatch):
    # When the extension returns unknown, fall back to X11
    monkeypatch.setattr(adapters.LinuxAdapter, "_gnome_extension_app", staticmethod(lambda: "unknown"))
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_app", staticmethod(lambda: "X11App"))
    monkeypatch.setattr(adapters.LinuxAdapter, "_activity_state", staticmethod(lambda: "active"))
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: None)

    snapshot = adapters.LinuxAdapter().snapshot()
    assert snapshot.app == "X11App"


def test_linux_activity_state_prefers_gnome_extension_idle_over_x11_screensaver(monkeypatch):
    # When the extension is available, it should be tried first
    monkeypatch.setattr(adapters.LinuxAdapter, "_gnome_extension_idle", lambda: 100_000)
    called_x11_screensaver = []
    monkeypatch.setattr(
        adapters.LinuxAdapter,
        "_x11_screensaver_idle",
        lambda: called_x11_screensaver.append(True) or 999_999,
    )

    assert adapters.LinuxAdapter._activity_state() == "active"
    assert len(called_x11_screensaver) == 0  # should not fall through when extension works


def test_linux_activity_state_falls_back_to_x11_screensaver_when_extension_unavailable(monkeypatch):
    monkeypatch.setattr(adapters.LinuxAdapter, "_gnome_extension_idle", lambda: None)
    monkeypatch.setattr(adapters.LinuxAdapter, "_x11_screensaver_idle", lambda: 301_000)
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: None)

    assert adapters.LinuxAdapter._activity_state() == "idle"


def _locked_gdbus(monkeypatch, reply: str):
    """gdbus present, org.gnome.ScreenSaver answering `reply`."""
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: reply)


def test_a_locked_screen_reports_locked_and_idle(monkeypatch):
    """The bug this exists for: locking is not five minutes of work.

    GNOME disables extensions on the lock screen, so the focused-app source
    disappears precisely when the screen locks, and locking also resets the
    idle timer. Without this check the collector recorded `unknown`/`active`
    for the first IDLE_AFTER_SECONDS of every lock.
    """
    _locked_gdbus(monkeypatch, "(true,)")
    snapshot = adapters.LinuxAdapter().snapshot()
    assert (snapshot.app, snapshot.activity_state) == ("locked", "idle")


def test_an_unlocked_screen_is_left_to_the_ordinary_path(monkeypatch):
    _locked_gdbus(monkeypatch, "(false,)")
    monkeypatch.setattr(adapters.LinuxAdapter, "_gnome_extension_app", staticmethod(lambda: "org.gnome.Terminal"))
    monkeypatch.setattr(adapters.LinuxAdapter, "_activity_state", staticmethod(lambda: "active"))
    snapshot = adapters.LinuxAdapter().snapshot()
    assert (snapshot.app, snapshot.activity_state) == ("org.gnome.Terminal", "active")


@pytest.mark.parametrize(
    "reply",
    [
        "(false,)",
        "",
        "not a variant",
        "falsey",
        # Contains "true" but does not say true. A substring test passes this
        # and reports the machine as locked.
        "(untrue,)",
        "Error: GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: true",
    ],
)
def test_only_a_true_reply_counts_as_locked(monkeypatch, reply):
    # A wrong "locked" erases real activity, so anything unrecognised is not it.
    _locked_gdbus(monkeypatch, reply)
    assert adapters.LinuxAdapter._screen_locked() is False


def test_a_screensaver_that_is_not_there_is_not_a_lock(monkeypatch):
    # No GNOME, no gdbus, or the call raising: all mean "cannot tell", and
    # cannot-tell must not be reported as locked.
    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: None)
    assert adapters.LinuxAdapter._screen_locked() is False

    monkeypatch.setattr(adapters.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setattr(subprocess, "check_output", _raise_oserror)
    assert adapters.LinuxAdapter._screen_locked() is False
