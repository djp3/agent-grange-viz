# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""scripts/sparkle_tools.sh: which of Sparkle's tools a release is signed with. They
are the ones SwiftPM fetched with the framework, and each must be the pinned file every
time, since one of them is handed the key every installed copy trusts."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "sparkle_tools.sh"
TOOLS = ("generate_appcast", "generate_keys", "sign_update")


def checkout(tmp_path, version="2.10.1", tools=None, resolved=True):
    """A stand-in checkout: the script, a Package.resolved that pins `version` of Sparkle,
    the tools SwiftPM would have fetched (`tools`: name -> what the file holds), and a
    `swift` that only notes what it was asked."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / SCRIPT.name)
    (tmp_path / "macos").mkdir()
    if resolved:
        pins = [{"identity": "h", "state": {"revision": "aa36", "version": "1.0.1"}},
                {"identity": "sparkle", "kind": "remoteSourceControl", "state": {"revision": "ab7d"}},
                {"identity": "swift-argument-parser", "state": {"revision": "6a52", "version": "1.8.2"}}]
        if version is not None:
            pins[1]["state"]["version"] = version
        (tmp_path / "macos" / "Package.resolved").write_text(json.dumps({"pins": pins}, indent=2))
    for name, holds in (tools or {}).items():
        tool = tmp_path / "macos" / ".build" / "artifacts" / "sparkle" / "Sparkle" / "bin" / name
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_text(holds)
        tool.chmod(0o755)
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "swift").write_text(f'#!/bin/bash\necho "$@" >> "{tmp_path}/swift-asked"\n')
    (fake / "swift").chmod(0o755)
    return tmp_path


def run(tree):
    env = {**os.environ, "PATH": f"{tree / 'fakebin'}:/usr/bin:/bin"}
    return subprocess.run(["/bin/bash", str(tree / "scripts" / SCRIPT.name)], capture_output=True, text=True,
                          timeout=60, env=env)


def asked_of_swift(tree) -> list[str]:
    noted = tree / "swift-asked"
    return noted.read_text().splitlines() if noted.exists() else []


def test_tools_that_are_not_the_pinned_files_are_refused_by_name(tmp_path):
    """Whatever became of the build folder since they arrived: an unpacking cut short,
    another version left behind, a file changed."""
    tree = checkout(tmp_path, tools={name: "not what was reviewed" for name in TOOLS})
    got = run(tree)
    assert got.returncode == 1 and got.stdout == ""
    assert "not the pinned files of Sparkle 2.10.1, refusing to use them" in got.stderr
    assert all(name in got.stderr for name in TOOLS)
    assert asked_of_swift(tree) == ["package --only-use-versions-from-resolved-file resolve"]      # tried once


def test_a_tool_that_is_missing_is_refused_as_one_that_is_wrong_is(tmp_path):
    got = run(checkout(tmp_path, tools={"generate_appcast": "x", "generate_keys": "x"}))
    assert got.returncode == 1 and got.stdout == "" and "sign_update" in got.stderr


def test_a_checkout_not_built_yet_has_swiftpm_fetch_what_is_pinned_and_nothing_newer(tmp_path):
    tree = checkout(tmp_path)
    got = run(tree)
    assert asked_of_swift(tree) == ["package --only-use-versions-from-resolved-file resolve"]
    assert got.returncode == 1 and got.stdout == ""                    # this stand-in swift fetches nothing
    assert "no Sparkle tools under macos/.build/artifacts" in got.stderr
    assert "swift package reset" in got.stderr                         # what to do when SwiftPM thinks it has them


def test_a_version_of_sparkle_with_no_pinned_hashes_is_refused_before_anything_is_fetched(tmp_path):
    tree = checkout(tmp_path, version="2.11.0", tools={name: "x" for name in TOOLS})
    got = run(tree)
    assert got.returncode == 1 and got.stdout == "" and asked_of_swift(tree) == []
    assert "no pinned SHA-256 for generate_appcast of Sparkle 2.11.0" in got.stderr


@pytest.mark.parametrize("how, said", [
    (dict(resolved=False), "macos/Package.resolved is missing"),
    (dict(version=None), "macos/Package.resolved pins no version of Sparkle")])
def test_it_never_stops_without_saying_why(tmp_path, how, said):
    """The version it could not find is said, not left as a silent exit."""
    got = run(checkout(tmp_path, **how))
    assert got.returncode == 1 and got.stdout == "" and said in got.stderr


def test_the_version_is_the_one_pinned_for_sparkle_not_a_neighbours(tmp_path):
    """Read from Package.resolved with awk alone: no interpreter that may be missing."""
    text = SCRIPT.read_text()
    assert "python" not in text and "head -1" not in text
    got = run(checkout(tmp_path, version="9.9.9"))
    assert "Sparkle 9.9.9" in got.stderr and "1.0.1" not in got.stderr and "1.8.2" not in got.stderr


def test_each_tool_has_a_pinned_hash_for_the_version_the_app_links():
    resolved = json.loads((ROOT / "macos" / "Package.resolved").read_text())
    version = next(pin["state"]["version"] for pin in resolved["pins"] if pin["identity"] == "sparkle")
    text = SCRIPT.read_text()
    for name in TOOLS:
        line = next(line for line in text.splitlines() if f"{version}/{name})" in line)
        pinned = line.split('"')[1]
        assert len(pinned) == 64 and set(pinned) <= set("0123456789abcdef"), name


def test_on_a_built_checkout_it_names_the_tools_swiftpm_fetched(tmp_path):
    """The real script on the real checkout, for the tools that are already there, and with
    a `swift` that refuses. With the real one, a build folder whose tools are gone, or are
    another version's, would have SwiftPM resolve the packages in the middle of the tests:
    from the network, and changing the checkout. The tests reach neither."""
    if not (ROOT / "macos" / ".build" / "artifacts").is_dir():
        pytest.skip("this checkout has not been built: nothing was fetched")
    refuses = tmp_path / "bin" / "swift"
    refuses.parent.mkdir()
    refuses.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/swift-asked"\n'
                       'echo "a test fetches nothing" >&2\nexit 97\n')
    refuses.chmod(0o755)
    got = subprocess.run(["/bin/bash", str(SCRIPT)], capture_output=True, text=True, timeout=120,
                         env={**os.environ, "PATH": f"{refuses.parent}:/usr/bin:/bin"})
    if (tmp_path / "swift-asked").exists():
        assert got.returncode != 0 and got.stdout == ""                 # it asked, was refused, and named nothing
        pytest.skip("the tools in macos/.build are not the pinned ones (or are gone): build again to have them")
    assert got.returncode == 0, got.stderr
    there = Path(got.stdout.strip())
    assert there.is_relative_to(ROOT / "macos" / ".build" / "artifacts")
    assert all(os.access(there / "bin" / name, os.X_OK) for name in TOOLS)

