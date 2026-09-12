"""Checks on the GNOME Shell extension that do not need a GNOME session.

The extension is JavaScript running inside the compositor, so none of the
Python tests reach it and it cannot be exercised on a development mac at all.
What is still checkable from here is the part that would be expensive to get
wrong: the privacy invariant, and the identifiers that three files have to
agree on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

EXTENSION_DIR = Path(__file__).resolve().parents[1] / "linux" / "gnome-extension"
METADATA = EXTENSION_DIR / "metadata.json"
SOURCE = EXTENSION_DIR / "extension.js"
INSTALLER = EXTENSION_DIR.parent / "install-gnome-extension.sh"

BUS_NAME = "org.activitycollector.Telemetry"
OBJECT_PATH = "/org/activitycollector/Telemetry"


def test_the_metadata_is_valid_json_with_the_fields_gnome_requires():
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    for field in ("uuid", "name", "description", "shell-version"):
        assert field in metadata, field
    # ESM and the Extension base class landed in 45, and the extension uses both
    assert min(int(v) for v in metadata["shell-version"]) >= 45


def test_the_installer_and_the_metadata_agree_on_the_uuid():
    # GNOME loads an extension from a directory named for its uuid, so the two
    # drifting apart installs something the shell will never look at.
    uuid = json.loads(METADATA.read_text(encoding="utf-8"))["uuid"]
    assert uuid in INSTALLER.read_text(encoding="utf-8")


def test_the_adapter_and_the_extension_agree_on_the_bus_name_and_path():
    from activity_collector import adapters

    source = Path(adapters.__file__).read_text(encoding="utf-8")
    javascript = SOURCE.read_text(encoding="utf-8")
    for identifier in (BUS_NAME, OBJECT_PATH):
        assert identifier in javascript, identifier
        assert identifier in source, identifier


@pytest.mark.parametrize(
    "forbidden",
    ["get_title", "get_description", "title", "get_sandboxed_app_id"],
)
def test_the_extension_never_reads_a_window_title(forbidden):
    """The one invariant of this project, enforced rather than reviewed.

    The extension runs inside the compositor, so unlike every other collector
    it *could* read window titles, document names and URLs. Nothing downstream
    would notice if it started to: the field is a string either way, and the
    title would just look like an unusually descriptive app name.
    """
    javascript = SOURCE.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in javascript.splitlines() if not line.lstrip().startswith("//")
    )
    assert forbidden not in code


def test_the_extension_gives_up_its_bus_name_when_disabled():
    # disable() is called on lock, not only on uninstall, so a name held past it
    # leaks across lock/unlock and the shell's review process rejects it.
    javascript = SOURCE.read_text(encoding="utf-8")
    body = javascript.split("disable()", 1)
    assert len(body) == 2, "no disable() in the extension"
    assert "bus_unown_name" in body[1]
    assert "unexport" in body[1]
