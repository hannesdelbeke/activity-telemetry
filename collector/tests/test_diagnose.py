"""The --diagnose entry point.

It exists to make a silent failure loud, so the thing worth testing is that it
stays loud: a machine where no probe answers has to exit non-zero, because that
exit code is what a check on the machine will look at.
"""

from __future__ import annotations

from activity_collector import __main__ as entry
from activity_collector.adapters import Snapshot


class _Adapter:
    def __init__(self, state, reading):
        self._state, self._reading = state, reading

    def snapshot(self):
        return Snapshot("org.gnome.Ptyxis.desktop", self._state)

    def idle_reading(self):
        return self._reading


def test_diagnose_exits_zero_and_names_the_probe_that_answered(monkeypatch, capsys):
    monkeypatch.setattr(entry, "create_adapter", lambda: _Adapter("active", ("gnome-extension", 1200)))
    assert entry.diagnose() == 0
    out = capsys.readouterr().out
    assert "gnome-extension" in out
    assert "1200" in out


def test_diagnose_exits_non_zero_when_nothing_could_measure_idle(monkeypatch, capsys):
    monkeypatch.setattr(entry, "create_adapter", lambda: _Adapter("unknown", ("none", None)))
    assert entry.diagnose() == 1
    assert "gnome-extensions list --enabled" in capsys.readouterr().out


def test_diagnose_works_on_an_adapter_with_no_idle_reading(monkeypatch, capsys):
    # mac and Windows adapters have no idle_reading; the diagnostic must still run.
    class Bare:
        def snapshot(self):
            return Snapshot("Safari", "active")

    monkeypatch.setattr(entry, "create_adapter", lambda: Bare())
    assert entry.diagnose() == 0
    assert "n/a" in capsys.readouterr().out
