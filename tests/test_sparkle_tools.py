# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""scripts/fetch_sparkle_tools.sh hands a release the tools it gives the update key to. These
run it, in a scratch checkout with a curl that reaches nothing, and see what it refuses."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "fetch_sparkle_tools.sh"
VERSION = "2.10.1"
TOOLS = ("generate_appcast", "generate_keys", "sign_update")
REAL = ROOT / ".sparkle-tools" / VERSION / "bin"         # there once a release, or a dry run, has fetched them

CURL = """#!/bin/sh
# A curl for the tests: it reaches nothing. It notes that it was called, and writes
# $CURL_WRITES to the file -o names when there is something to write.
echo "curl $*" >> "$CURL_LOG"
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out=$2; shift; done
if [ -n "${CURL_WRITES:-}" ] && [ -n "$out" ]; then printf '%s' "$CURL_WRITES" > "$out"; exit 0; fi
exit 22
"""


def shim(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def checkout(tmp_path):
    """As much of a checkout as the script reads: itself, and the pin in macos/."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / SCRIPT.name)
    (tmp_path / "macos").mkdir()
    (tmp_path / "macos" / "Package.resolved").write_text(json.dumps(
        {"pins": [{"identity": "sparkle", "state": {"version": VERSION}}]}))
    (tmp_path / "macos" / "Package.swift").write_text(
        f'        .package(url: "https://github.com/sparkle-project/Sparkle.git", from: "{VERSION}"),\n')
    (tmp_path / "bin").mkdir()
    shim(tmp_path / "bin" / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    shim(tmp_path / "bin" / "curl", CURL)
    return tmp_path


def run(checkout: Path, **env) -> subprocess.CompletedProcess:
    env = {"PATH": f"{checkout / 'bin'}:/usr/bin:/bin", "CURL_LOG": str(checkout / "curl.log"), **env}
    return subprocess.run(["/bin/bash", str(checkout / "scripts" / SCRIPT.name)], capture_output=True, text=True, env=env)


def place(checkout: Path, **contents) -> Path:
    """Tools where an earlier fetch would have left them. A tool is the real one, when this
    checkout has it, unless `contents` gives its bytes; None leaves it out."""
    there = checkout / ".sparkle-tools" / VERSION / "bin"
    there.mkdir(parents=True)
    for tool in TOOLS:
        if tool in contents:
            if contents[tool] is not None:
                (there / tool).write_bytes(contents[tool])
                (there / tool).chmod(0o755)
        else:
            shutil.copy(REAL / tool, there / tool)
    return there


def called(checkout: Path) -> bool:
    return (checkout / "curl.log").exists()


needs_the_real_tools = pytest.mark.skipif(not all((REAL / t).is_file() for t in TOOLS),
                                          reason="Sparkle's tools are not in .sparkle-tools/ (no release was dry-run here)")
FAKE = {tool: b"not Sparkle's " + tool.encode() for tool in TOOLS}


# ---- every run checks every tool ------------------------------------------------------------------
@needs_the_real_tools
def test_the_tools_as_sparkle_shipped_them_are_handed_back(checkout):
    place(checkout)
    done = run(checkout)
    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()) == (checkout / ".sparkle-tools" / VERSION).resolve()
    assert not called(checkout), "they were there already: nothing is downloaded"


def test_tools_that_are_not_sparkles_are_refused(checkout):
    """.sparkle-tools/ is ignored by git, so the release's clean-tree check does not see it.
    The script used to trust whatever was executable at bin/generate_appcast."""
    place(checkout, **FAKE)
    done = run(checkout)
    assert done.returncode == 1 and done.stdout == "", "no folder is handed to the release"
    assert f"bin/generate_appcast is not the generate_appcast of Sparkle {VERSION}" in done.stderr
    assert "refusing to use it" in done.stderr and not called(checkout)


@needs_the_real_tools
@pytest.mark.parametrize("changed", TOOLS)
def test_one_tool_changed_among_the_real_ones_is_caught(checkout, changed):
    there = place(checkout)
    with open(there / changed, "ab") as f:
        f.write(b"\0")
    done = run(checkout)
    assert done.returncode == 1 and done.stdout == ""
    assert f"bin/{changed} is not the {changed} of Sparkle {VERSION}" in done.stderr


@needs_the_real_tools
def test_what_an_unpacking_cut_short_left_behind_is_refused(checkout):
    """The old script unpacked straight into place: stopped part-way it left a generate_appcast
    that was there and executable, and every later run took it for the tools."""
    half = (REAL / "generate_appcast").read_bytes()[:4096]
    place(checkout, generate_appcast=half, sign_update=None)
    done = run(checkout)
    assert done.returncode == 1 and done.stdout == "" and "generate_appcast is not the generate_appcast" in done.stderr


@needs_the_real_tools
def test_a_tool_that_is_not_there_is_said_to_be_missing(checkout):
    place(checkout, sign_update=None)
    done = run(checkout)
    assert done.returncode == 1 and "bin/sign_update is missing" in done.stderr and "delete" in done.stderr


# ---- the download ------------------------------------------------------------------------------------
def test_a_download_that_is_not_the_pinned_file_is_not_unpacked(checkout):
    done = run(checkout, CURL_WRITES="not the tarball")
    assert called(checkout) and f"Sparkle-{VERSION}.tar.xz" in (checkout / "curl.log").read_text()
    assert done.returncode == 1 and done.stdout == ""
    assert "does not match the pinned SHA-256; refusing to unpack it" in done.stderr
    assert not (checkout / ".sparkle-tools").exists() or not any((checkout / ".sparkle-tools").iterdir()), \
        "nothing is left that a later run could take for tools"


def test_a_download_that_fails_leaves_nothing_behind(checkout):
    done = run(checkout)
    assert done.returncode != 0 and done.stdout == "" and called(checkout)
    assert not (checkout / ".sparkle-tools" / VERSION).exists()


def test_a_version_with_no_pinned_hash_is_refused_before_anything_is_fetched(checkout):
    done = run(checkout, SPARKLE_VERSION="2.11.0")
    assert done.returncode == 1 and "no pinned SHA-256 for Sparkle 2.11.0" in done.stderr
    assert not called(checkout)
