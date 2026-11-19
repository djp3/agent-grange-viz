# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the app keeps its files, from a checkout and as the bundled Mac app, and the
version it says it is."""
from pathlib import Path

import apppaths


def test_a_checkout_keeps_everything_beside_the_code(monkeypatch):
    monkeypatch.setattr(apppaths, "FROZEN", False)
    assert apppaths.data_dir() == apppaths.CODE_DIR
    assert apppaths.log_file() == apppaths.CODE_DIR / "logs" / "agent-grange-viz.log"


def test_the_mac_app_keeps_them_in_the_library(monkeypatch):
    monkeypatch.setattr(apppaths, "FROZEN", True)
    library = Path.home() / "Library"
    assert apppaths.data_dir() == library / "Application Support" / "agent-grange-viz"
    assert apppaths.log_file() == library / "Logs" / "agent-grange-viz" / "agent-grange-viz.log"


def test_the_mac_app_says_which_version_it_is(monkeypatch):
    monkeypatch.setenv("AGENT_GRANGE_VIZ_VERSION", "v9.8.7")
    assert apppaths.app_version() == "9.8.7"


def test_a_checkout_reads_the_version_file(monkeypatch):
    monkeypatch.delenv("AGENT_GRANGE_VIZ_VERSION", raising=False)
    assert apppaths.app_version() == (apppaths.CODE_DIR / "VERSION").read_text().strip().removeprefix("v")


def test_no_version_file_is_no_version(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_GRANGE_VIZ_VERSION", raising=False)
    monkeypatch.setattr(apppaths, "CODE_DIR", tmp_path)
    assert apppaths.app_version() == ""
