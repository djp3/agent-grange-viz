# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Mac app needs macOS 27: the two places that declare it agree, the build refuses an app
in which anything needs more, and someone on an earlier macOS is told so and what works there
(the README, and scripts/build_app.sh before it builds anything)."""
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MINIMUM = "27.0"
TOOLS = {"PATH": "/usr/bin:/bin:/usr/sbin"}


def test_the_declared_minimum_matches_the_swift_package():
    declared = plistlib.loads((ROOT / "macos" / "Info.plist").read_bytes())["LSMinimumSystemVersion"]
    swift = re.search(r'\.macOS\("([\d.]+)"\)', (ROOT / "macos" / "Package.swift").read_text()).group(1)
    assert declared == swift == MINIMUM


def test_the_readme_says_what_is_needed_and_what_works_below_it():
    readme = (ROOT / "README.md").read_text()
    assert "macOS 27+" in readme and "macOS 27 (Golden Gate) or later" in readme
    below = readme[readme.index("**On macOS 26 (Tahoe) or earlier?**"):]
    assert "needs macOS 27.0 or later" in below.replace("\n", " ")
    assert "python3.14 -m venv .venv" in below and ".venv/bin/python main.py" in below
    assert "Check for Updates" not in readme, "this app has no updater to speak of"


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
def test_the_build_goes_ahead_on_macos_27_or_later(enough, tmp_path):
    """Only as far as the first step: a Python that does nothing stands in for the real one."""
    stop = tmp_path / "python3"
    stop.write_text("#!/bin/sh\necho stand-in\nexit 7\n")
    stop.chmod(0o755)
    run = subprocess.run([ROOT / "scripts" / "build_app.sh"], capture_output=True, text=True,
                         env=dict(TOOLS, MACOS_VERSION=enough, PYTHON=str(stop)))
    assert "needs macOS" not in run.stderr
    assert "python core" in run.stdout and run.returncode == 7, "it reached the first build step"


def _bundle(folder, declared):
    app = folder / "x.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"LSMinimumSystemVersion": declared}))
    shutil.copy("/bin/ls", app / "Contents" / "MacOS" / "ls")              # a real Mach-O, both architectures
    (app / "Contents" / "MacOS" / "notes.txt").write_text("not a binary")
    return app


@pytest.mark.skipif(shutil.which("otool") is None, reason="needs the Xcode command line tools")
def test_a_binary_that_needs_a_newer_macos_fails_the_build(tmp_path):
    check = ROOT / "scripts" / "check_macos_minimum.sh"
    run = subprocess.run([check, _bundle(tmp_path / "old", "10.0")], capture_output=True, text=True)
    assert run.returncode == 1 and "Contents/MacOS/ls" in run.stderr
    assert "need a newer macOS than 10.0" in run.stderr
    run = subprocess.run([check, _bundle(tmp_path / "new", "99.0")], capture_output=True, text=True)
    assert run.returncode == 0 and "all 1 programs" in run.stdout


@pytest.mark.skipif(shutil.which("otool") is None, reason="needs the Xcode command line tools")
def test_the_check_compares_versions_as_numbers(tmp_path):
    """What /bin/ls needs is this macOS's own version, written 27.0: a minimum written 27 is
    the same number, and must not read as older."""
    check = ROOT / "scripts" / "check_macos_minimum.sh"
    app = _bundle(tmp_path, "0")
    said = subprocess.run([check, app], capture_output=True, text=True).stderr
    needs = re.search(r"needs macOS ([\d.]+): Contents/MacOS/ls", said).group(1)
    major, minor = (needs.split(".") + ["0"])[:2]
    if minor != "0":
        pytest.skip(f"ls needs {needs}: no shorter way to write that number")
    for same in (major, f"{major}.0", f"{major}.0.0"):
        run = subprocess.run([check, app, same], capture_output=True, text=True)
        assert run.returncode == 0, f"{needs} is not newer than {same}"
