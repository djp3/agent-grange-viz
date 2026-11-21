# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""scripts/check_sparkle_key.sh, which a release runs before it builds anything: is the key it
would sign with the one installed copies trust? These run it against a generate_keys of their
own, so no keychain is asked anything, and against a stand-in for the script that works out
an exported key's public half (scripts/sparkle_public_key.sh, which has tests of its own in
test_release_scripts.py)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sparkle_standins import ANOTHER_KEY, GENERATE_KEYS, PUBLIC_HALF, THE_APPS_KEY, shim

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_sparkle_key.sh"

@pytest.fixture
def checkout(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / SCRIPT.name)
    (tmp_path / "macos").mkdir()
    shutil.copy(ROOT / "macos" / "Info.plist", tmp_path / "macos" / "Info.plist")
    shim(tmp_path / "tools" / "bin" / "generate_keys", GENERATE_KEYS)
    shim(tmp_path / "scripts" / "sparkle_public_key.sh", PUBLIC_HALF)
    return tmp_path


def run(checkout: Path, cwd: Path | None = None, tools: str | None = None, account: str = "agent-grange-viz",
        key_file: str | None = None, **env) -> subprocess.CompletedProcess:
    """Run the script as a release does: told which key by the arguments it signs with, the
    keychain's under `account`, or `key_file` when one is given. From `cwd` when given,
    naming the tools folder as `tools` when given."""
    env = {"PATH": "/usr/bin:/bin", "ASKED": str(checkout / "asked"),
           "ASKED_OF_FILE": str(checkout / "asked-of-file"), **env}
    which = ["--ed-key-file", key_file] if key_file is not None else ["--account", account]
    return subprocess.run(["/bin/bash", str(checkout / "scripts" / SCRIPT.name), tools or str(checkout / "tools"),
                           *which], capture_output=True, text=True, env=env, cwd=cwd)


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
    run(checkout, ANSWER=THE_APPS_KEY, account="another-account")
    assert asked(checkout)[-1] == "--account another-account -p"


def test_which_key_is_not_this_scripts_to_decide(checkout):
    """The release decides once and says so in the arguments it signs with. Settings the
    release reads are nothing to this script: with a say of its own, it could vouch for one
    key while the appcast was signed with another."""
    done = run(checkout, ANSWER=THE_APPS_KEY, SPARKLE_ACCOUNT="set-and-ignored", SPARKLE_PRIVATE_KEY_FILE="/nowhere")
    assert done.returncode == 0 and asked(checkout) == ["--account agent-grange-viz -p"]
    script = SCRIPT.read_text()
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert "SPARKLE_ACCOUNT" not in code and "SPARKLE_PRIVATE_KEY_FILE" not in code
    for wrong in (["--keychain", "x"], ["--account"], []):
        got = subprocess.run(["/bin/bash", str(checkout / "scripts" / SCRIPT.name), str(checkout / "tools"), *wrong],
                             capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        assert got.returncode != 0 and "usage: check_sparkle_key.sh" in got.stderr, wrong


def test_a_key_that_is_not_the_apps_stops_the_release(checkout):
    done = run(checkout, ANSWER=ANOTHER_KEY)
    assert done.returncode == 1 and done.stdout == ""
    assert "is not the one in macos/Info.plist" in done.stderr and "'agent-grange-viz'" in done.stderr


def test_no_key_under_the_account_stops_the_release(checkout):
    done = run(checkout)
    assert done.returncode == 1
    assert "no Sparkle key in the login keychain under the account 'agent-grange-viz'" in done.stderr


@pytest.mark.parametrize("fails", [
    "Access denied. Can't get keys from the keychain.\nGo to Keychain Access.app, lock the login keychain, then "
    "unlock it again.",
    "You've cancelled the request to read the key from the Keychain. Please run the tool again.",
    "The operating system has blocked access to the Keychain."])
def test_a_key_that_is_there_and_could_not_be_read_is_not_called_missing(checkout, fails):
    """generate_keys says why on standard output, which was thrown away: a keychain that was
    locked, or a prompt that was refused, read as "no Sparkle key under the account"."""
    done = run(checkout, FAILS=fails)
    assert done.returncode == 1 and done.stdout == ""
    assert "no Sparkle key in the login keychain" not in done.stderr
    assert "could not be had from the login keychain. generate_keys said:" in done.stderr
    assert all(line in done.stderr for line in fails.splitlines())


def test_a_key_file_is_looked_for_and_the_keychain_left_alone(checkout):
    key = checkout / "exported.key"
    key.write_text("made up")
    done = run(checkout, key_file=str(key), FILE_HAS=THE_APPS_KEY)
    assert done.returncode == 0 and str(key) in done.stdout and asked(checkout) == []
    assert (checkout / "asked-of-file").read_text().splitlines() == [str(key)]
    gone = run(checkout, key_file=str(checkout / "nowhere.key"))
    assert gone.returncode == 1 and "names no file that can be read" in gone.stderr and asked(checkout) == []


def test_a_key_file_whose_key_is_not_the_apps_stops_the_release_as_the_keychains_would(checkout):
    """None of Sparkle's tools says a file's public half, so this used to be left to
    generate_appcast, after the build: it leaves the zip unsigned and goes on."""
    key = checkout / "exported.key"
    key.write_text("made up")
    done = run(checkout, key_file=str(key), FILE_HAS=ANOTHER_KEY)
    assert done.returncode == 1 and done.stdout == "" and asked(checkout) == []
    assert f"the Sparkle key in {key} is not the one in macos/Info.plist" in done.stderr


def test_what_is_named_from_where_it_was_called_is_found_from_there(checkout):
    """The script moves to the checkout before it does anything. Run by hand from somewhere
    else, a tools folder or a key file named from there was looked for in the checkout."""
    elsewhere = checkout / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    (elsewhere / "exported.key").write_text("made up")
    done = run(checkout, cwd=elsewhere, tools="../../tools", ANSWER=THE_APPS_KEY)
    assert done.returncode == 0, done.stderr                               # the keychain's, by tools named from there
    assert asked(checkout) == ["--account agent-grange-viz -p"]
    done = run(checkout, cwd=elsewhere, tools="../../tools", key_file="exported.key",
               FILE_HAS=THE_APPS_KEY)
    assert done.returncode == 0, done.stderr
    assert (checkout / "asked-of-file").read_text().splitlines() == [str(elsewhere / "exported.key")]


def test_a_key_on_standard_input_or_a_pipe_is_refused_for_what_it_is(checkout):
    """Sparkle's tools take either. A release reads the key three times, and a pipe is empty
    after the first: said as that, not as a file that cannot be found."""
    pipe = checkout / "a-pipe"
    os.mkfifo(pipe)
    for given in ("-", str(pipe), "/dev/null"):
        done = run(checkout, key_file=given)
        assert done.returncode == 1 and done.stdout == "" and asked(checkout) == [], given
        assert "the key must be a file" in done.stderr and "reads the key more than once" in done.stderr, given
        assert "names no file" not in done.stderr and not (checkout / "asked-of-file").exists()


def test_a_file_that_holds_no_key_stops_the_release(checkout):
    key = checkout / "exported.key"
    key.write_text("not a key")
    done = run(checkout, key_file=str(key))
    assert done.returncode == 1 and done.stdout == "" and "does not hold a Sparkle key" in done.stderr
