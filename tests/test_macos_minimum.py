# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Someone on a macOS before the one the app needs is told so, and told what works there: in
the README, and by scripts/build_app.sh before it builds anything. (That the app itself is
held to its minimum is scripts/check_min_macos.py's, and tests/test_check_min_macos.py's.)"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = {"PATH": "/usr/bin:/bin:/usr/sbin"}


def test_the_readme_says_what_works_below_the_minimum():
    readme = (ROOT / "README.md").read_text()
    below = readme[readme.index("**On macOS 26 (Tahoe) or earlier?**"):]
    assert "needs macOS 27.0 or later" in below.replace("\n", " ")
    assert "python3.14 -m venv .venv" in below and ".venv/bin/python main.py" in below
    assert "it gets no automatic updates" in below.replace("\n", " "), "run from source, nothing updates it"


@pytest.mark.parametrize("older", ["26.4", "26.0", "15.7.2", "13.0"])
def test_the_build_says_so_on_an_earlier_macos_and_builds_nothing(older):
    run = subprocess.run([ROOT / "scripts" / "build_app.sh"], capture_output=True, text=True,
                         env=dict(TOOLS, MACOS_VERSION=older))
    assert run.returncode == 1
    said = run.stderr.replace("\n", " ")
    assert f"needs macOS 27 or later, and this Mac runs macOS {older}" in said
    assert ".venv/bin/python main.py" in said and "README.md" in said
    assert run.stdout == "", "no build step was started"


@pytest.mark.parametrize("enough", ["27.0", "27.2.1", "28.0"])
def test_the_build_goes_ahead_on_the_minimum_or_later(enough, tmp_path):
    """Only as far as the first step: a Python that does nothing stands in for the real one."""
    stop = tmp_path / "python3"
    stop.write_text("#!/bin/sh\necho stand-in\nexit 7\n")
    stop.chmod(0o755)
    run = subprocess.run([ROOT / "scripts" / "build_app.sh"], capture_output=True, text=True,
                         env=dict(TOOLS, MACOS_VERSION=enough, PYTHON=str(stop)))
    assert "needs macOS" not in run.stderr
    assert "python core" in run.stdout and run.returncode == 7, "it reached the first build step"
