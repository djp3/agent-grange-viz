# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Two hazards in the build and release scripts, each shown by running the script's own lines
or reading every pipe in them: a compile judged by a search of what it printed, and a pipe
whose reader leaves early. Both end a script under `set -euo pipefail` with nothing said.
(agent-frolic's tests, where the same scripts had the same faults.)"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD = (ROOT / "scripts" / "build_app.sh").read_text()


# ---- the compiler's verdict ------------------------------------------------------------------------
def swift_step(tmp_path, swift: str) -> subprocess.CompletedProcess:
    """Run the build's own lines that compile the host, with this stand-in for `swift`."""
    start = BUILD.index("SWIFT_LOG=build/swift-build.log")
    end = BUILD.index('[ -x "$BIN" ]', start)
    (tmp_path / "macos").mkdir()
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "swift").write_text("#!/bin/sh\n" + swift)
    (tmp_path / "bin" / "swift").chmod(0o755)
    return subprocess.run(["/bin/bash", "-euo", "pipefail", "-c", BUILD[start:end] + "echo went on\n"],
                          capture_output=True, text=True, cwd=tmp_path, timeout=60,
                          env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin"})


def test_a_compile_that_works_is_not_failed_for_what_it_did_not_print(tmp_path):
    """Its output was piped into a search, and under pipefail a search that finds nothing
    fails the pipe: a build that had worked would have ended there, with nothing said."""
    done = swift_step(tmp_path, 'echo "Compiling AgentGrangeViz main.swift"\n')
    assert done.returncode == 0 and done.stdout == "went on\n", done.stderr


def test_a_compile_that_works_shows_the_lines_worth_a_glance(tmp_path):
    done = swift_step(tmp_path, 'echo "warning: unreachable code"; echo "Compiling"; echo "Build complete! (2.1s)"\n')
    assert done.returncode == 0 and done.stdout.splitlines() == ["warning: unreachable code", "Build complete! (2.1s)",
                                                                  "went on"]


def test_a_compile_that_fails_stops_the_build_and_shows_all_it_said(tmp_path):
    done = swift_step(tmp_path, 'echo "main.swift:3: error: no such module"; echo "and a note beneath it"; exit 1\n')
    assert done.returncode == 1 and "went on" not in done.stdout
    assert "main.swift:3: error: no such module" in done.stderr and "and a note beneath it" in done.stderr
    assert "swift build failed" in done.stderr
    assert "PIPESTATUS" not in BUILD                                    # the check that could never run is gone


def test_the_build_keeps_to_the_versions_that_are_pinned():
    assert "swift build --only-use-versions-from-resolved-file -c release --product AgentGrangeViz" in BUILD


# ---- pipes whose reader leaves early ---------------------------------------------------------------
# A reader that may leave before its writer has finished: the writer is then cut off, the pipe
# fails under pipefail, and in an assignment under `set -e` the script ends with nothing said.
LEAVES_EARLY = [
    re.compile(r"head\b"),                                               # after its lines
    re.compile(r"grep\b[^|]*\s(-[a-zA-Z]*[qlLm]|--quiet|--silent|--max-count|--files-with)"),   # at a match
    re.compile(r"sed\b[^|]*(^|[\s;'\"{])[0-9$]*q\b"),                      # at its q
    re.compile(r"awk\b[^|]*\bexit\b"),                                   # at its exit
]


def leaves_early(line: str) -> str:
    """The first reader on this line of shell that may leave early, or "" when none does."""
    for reader in re.split(r"(?<!\|)\|(?!\|)", line)[1:]:
        reader = reader.strip()
        if any(pattern.match(reader) for pattern in LEAVES_EARLY):
            return reader
    return ""


@pytest.mark.parametrize("line", [
    'found=$(find . -name x | head -1)', 'first=$(list | head -n 1)', 'if file -b "$f" | grep -q "Mach-O"; then',
    'list | grep -m1 wanted', 'list | grep --max-count=1 wanted', 'list | grep -l wanted', 'list | sed q',
    "list | sed -n '3q;p'", "ID=$(security find-identity | awk '/Developer ID/ {print $2; exit}')",
    "list | awk '{ print $2; exit }'", "a | sort | head -3"])
def test_a_reader_that_may_leave_early_is_known_however_it_is_written(line):
    assert leaves_early(line), line


@pytest.mark.parametrize("line", [
    'found=$(find . -name x -print -quit)', "ID=$(security find-identity | awk '/Developer ID/ && !n++ {print $2}')",
    'list | sort | while read -r f; do', '[ "$(shasum -a 256 "$f" | cut -d" " -f1)" = "$WANT" ]',
    'printf "%s\\n" "$HAVE" | sed "s/^/    /" >&2', 'case "${KEY:-}" in ""|/*|-) ;; *) KEY="$PWD/$KEY" ;; esac',
    'a || b', 'grep -q wanted file || exit 1', '[ "$(head -c 8 "$picture" | xxd -p)" = 89504e47 ]',
    "list | grep -E 'error|warning' | tr a b", "list | sed 's/quit/q/'", "printf '%s' \"$A\" | sort -V | sed -n 1p"])
def test_a_reader_that_reads_to_the_end_is_left_alone(line):
    assert not leaves_early(line), line


def test_no_pipe_in_the_scripts_can_end_one_of_them_unheard():
    """Every pipe in every script that runs under pipefail, read for the hazard itself and
    not for the spellings that were once there."""
    sign = (ROOT / "scripts" / "sign_app.sh").read_text()
    assert 'kind=$(file -b "$f")' in sign and 'case "$kind" in *Mach-O*) sign "$f" ;; esac' in sign
    looked_at = 0
    for script in sorted((ROOT / "scripts").glob("*.sh")):
        lines = [line for line in script.read_text().splitlines() if not line.lstrip().startswith("#")]
        if not any("pipefail" in line for line in lines):
            continue
        looked_at += 1
        for line in lines:
            assert not leaves_early(line), f"{script.name}: `{leaves_early(line)}` may leave early: {line.strip()[:90]}"
    assert looked_at >= 5
