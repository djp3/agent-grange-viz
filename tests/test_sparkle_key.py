# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""scripts/check_sparkle_key.sh, which a release runs before it builds anything: is the key it
would sign with the one installed copies trust? These run it against a generate_keys of their
own, so no keychain is asked anything."""
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

from sparkle_standins import GENERATE_KEYS, shim

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_sparkle_key.sh"
with open(ROOT / "macos" / "Info.plist", "rb") as _f:
    THE_APPS_KEY = plistlib.load(_f)["SUPublicEDKey"]
ANOTHER_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="        # made up: thirty-two zero bytes

@pytest.fixture
def checkout(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / SCRIPT.name)
    (tmp_path / "macos").mkdir()
    shutil.copy(ROOT / "macos" / "Info.plist", tmp_path / "macos" / "Info.plist")
    shim(tmp_path / "tools" / "bin" / "generate_keys", GENERATE_KEYS)
    return tmp_path


def run(checkout: Path, **env) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "ASKED": str(checkout / "asked"), **env}
    return subprocess.run(["/bin/bash", str(checkout / "scripts" / SCRIPT.name), str(checkout / "tools")],
                          capture_output=True, text=True, env=env)


def asked(checkout: Path) -> list:
    path = checkout / "asked"
    return path.read_text().splitlines() if path.exists() else []


def test_the_key_the_app_trusts_passes(checkout):
    done = run(checkout, ANSWER=THE_APPS_KEY)
    assert done.returncode == 0, done.stderr
    assert "account agent-grange-viz" in done.stdout


def test_the_keychain_is_asked_by_this_apps_account_and_only_for_the_public_half(checkout):
    """A login keychain can hold another project's Sparkle key under no account name. Asked
    without an account, generate_keys answers with that one."""
    run(checkout, ANSWER=THE_APPS_KEY)
    assert asked(checkout) == ["--account agent-grange-viz -p"]
    run(checkout, ANSWER=THE_APPS_KEY, SPARKLE_ACCOUNT="another-account")
    assert asked(checkout)[-1] == "--account another-account -p"


def test_a_key_that_is_not_the_apps_stops_the_release(checkout):
    done = run(checkout, ANSWER=ANOTHER_KEY)
    assert done.returncode == 1 and done.stdout == ""
    assert "is not the one in macos/Info.plist" in done.stderr and "'agent-grange-viz'" in done.stderr


def test_no_key_under_the_account_stops_the_release(checkout):
    done = run(checkout)
    assert done.returncode == 1 and "no Sparkle key in the login keychain under the account 'agent-grange-viz'" in done.stderr


def test_a_key_file_is_looked_for_and_the_keychain_left_alone(checkout):
    key = checkout / "exported.key"
    key.write_text("made up")
    done = run(checkout, SPARKLE_PRIVATE_KEY_FILE=str(key))
    assert done.returncode == 0 and str(key) in done.stdout and asked(checkout) == []
    gone = run(checkout, SPARKLE_PRIVATE_KEY_FILE=str(checkout / "nowhere.key"))
    assert gone.returncode == 1 and "names no file that can be read" in gone.stderr and asked(checkout) == []
