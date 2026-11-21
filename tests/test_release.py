# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""scripts/release.sh, run for real in a scratch checkout. Everything outside it is a
stand-in that notes how it was called: the keychain, git, GitHub, Apple's notary, the build,
Sparkle's tools. The script itself, scripts/check_appcast.py and scripts/check_sparkle_key.sh
are the real ones. So these show what a release does and what it refuses to do, which a test
that only reads the script cannot: nothing is tagged or published unless the zip and the feed
are both signed, and nothing is built with a key that is not the app's."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sparkle_standins import ANOTHER_KEY, GENERATE_KEYS, PUBLIC_HALF, THE_APPS_KEY, appcast, shim

ROOT = Path(__file__).resolve().parent.parent
VERSION = "9.9.9"

NOTES_A_CALL = '#!/bin/sh\necho "{name} $*" >> "$CALLS"\n'
GIT = NOTES_A_CALL.format(name="git") + """case "$1 $2" in
    "status --porcelain") [ -n "${DIRTY:-}" ] && echo "$DIRTY"; exit 0 ;;
    "rev-parse -q") exit 1 ;;                 # no such tag here
    "ls-remote --exit-code") exit 2 ;;        # nor on origin
esac
exit 0
"""
GH = NOTES_A_CALL.format(name="gh") + '[ "$1 $2" = "release view" ] && exit 1      # no such release yet\nexit 0\n'
SECURITY = ('#!/bin/sh\n'
            'echo \'  1) ABCDEF0123456789ABCDEF0123456789ABCDEF01 "Developer ID Application: Made Up (TEAM123456)"\'\n')
# scripts/build_app.sh: it notes how it was called, and whether it was asked for the copy
# that updates itself (WITH_UPDATES), which only a release is.
# The app it leaves has the source's Info.plist, and loses its feed unless it was asked for the
# copy that updates itself, as the real build does. $BUILD_LOSES names keys it leaves out besides.
BUILD = ('#!/bin/sh\necho "build_app $* WITH_UPDATES=${WITH_UPDATES:-}" >> "$CALLS"\n'
         "mkdir -p dist/agent-grange-viz.app/Contents\n"
         "cp macos/Info.plist dist/agent-grange-viz.app/Contents/Info.plist\n"
         '[ "${WITH_UPDATES:-0}" = 1 ] || BUILD_LOSES="SUFeedURL ${BUILD_LOSES:-}"\n'
         'for key in ${BUILD_LOSES:-}; do\n'
         '    /usr/libexec/PlistBuddy -c "Delete :$key" dist/agent-grange-viz.app/Contents/Info.plist\n'
         "done\n")
GENERATE_APPCAST = NOTES_A_CALL.format(name="generate_appcast") + """out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out=$2; shift; done
cp "$APPCAST" "$out"
"""
# Sparkle's sign_update: when it fails ($VERIFIES) it says why on standard output ($SAYS), as the real one does.
SIGN_UPDATE = (NOTES_A_CALL.format(name="sign_update")
               + '[ "${VERIFIES:-0}" = 0 ] || echo "${SAYS:-Error: failed to verify}"\nexit ${VERIFIES:-0}\n')
# scripts/sparkle_tools.sh refuses on its $TOOLS_REFUSED_AT'th call, when that is set.
TOOLS_OR_NOT = """n=$(grep -c '^sparkle_tools' "$CALLS")
if [ "$n" = "${TOOLS_REFUSED_AT:-}" ]; then echo "refusing to use it" >&2; exit 1; fi
"""


class Release:
    def __init__(self, tmp_path: Path):
        self.where = tmp_path
        self.root = root = tmp_path / "checkout"
        (root / "scripts").mkdir(parents=True)
        for real in ("release.sh", "check_appcast.py", "check_sparkle_key.sh"):
            shutil.copy(ROOT / "scripts" / real, root / "scripts" / real)
        (root / "macos").mkdir()
        shutil.copy(ROOT / "macos" / "Info.plist", root / "macos" / "Info.plist")
        (root / "VERSION").write_text(VERSION + "\n")
        (root / "release-notes").mkdir()
        (root / "release-notes" / f"{VERSION}.md").write_text("A made-up release.\n\n- one thing\n")
        shim(root / "scripts" / "sparkle_tools.sh", NOTES_A_CALL.format(name="sparkle_tools") + TOOLS_OR_NOT
             + f'echo "{root}/tools"\n')
        shim(root / "scripts" / "locked_venv.sh", NOTES_A_CALL.format(name="locked_venv") + "echo /nowhere/python\n")
        shim(root / "scripts" / "build_app.sh", BUILD)
        shim(root / "scripts" / "sparkle_public_key.sh", PUBLIC_HALF)
        shim(root / "tools" / "bin" / "generate_keys", GENERATE_KEYS)
        shim(root / "tools" / "bin" / "generate_appcast", GENERATE_APPCAST)
        shim(root / "tools" / "bin" / "sign_update", SIGN_UPDATE)
        for name, text in (("security", SECURITY), ("git", GIT), ("gh", GH),
                           ("xcrun", NOTES_A_CALL.format(name="xcrun")), ("spctl", NOTES_A_CALL.format(name="spctl")),
                           ("python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')):
            shim(root / "bin" / name, text)
        self.feed = tmp_path / "what-generate_appcast-writes.xml"
        self.feed.write_bytes(appcast())

    def run(self, dry: bool = False, **env) -> subprocess.CompletedProcess:
        env = {"PATH": f"{self.root / 'bin'}:/usr/bin:/bin", "CALLS": str(self.where / "calls"),
               "ASKED": str(self.where / "asked"), "ANSWER": THE_APPS_KEY, "APPCAST": str(self.feed),
               "DRY_RUN": "1" if dry else "0", **env}
        env = {k: v for k, v in env.items() if v is not None}
        return subprocess.run(["/bin/bash", str(self.root / "scripts" / "release.sh")], capture_output=True, text=True,
                              env=env, cwd=self.where)

    @property
    def calls(self) -> list:
        path = self.where / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def called(self, start: str) -> list:
        return [c for c in self.calls if c.startswith(start)]

    def at(self, start: str) -> int:
        return next(i for i, c in enumerate(self.calls) if c.startswith(start))

    def nothing_left_this_mac(self) -> bool:
        """Nothing was tagged, pushed, published, or sent to Apple."""
        return not (self.called("git tag") or self.called("git push") or self.called("gh release create")
                    or self.called("xcrun notarytool submit"))


@pytest.fixture
def release(tmp_path):
    return Release(tmp_path)


# ---- what a release does -----------------------------------------------------------------------------
def test_a_release_tags_and_publishes_the_zip_and_the_appcast_together(release):
    done = release.run()
    assert done.returncode == 0, done.stderr
    assert f"released: https://github.com/djp3/agent-grange-viz/releases/tag/v{VERSION}" in done.stdout
    published = release.called("gh release create")[0]
    assert published.endswith(f"dist/agent-grange-viz-{VERSION}.zip dist/appcast/appcast.xml"), \
        "releases/latest/download/appcast.xml is the feed the app polls"
    assert f"gh release create v{VERSION} --repo djp3/agent-grange-viz" in published
    order = [release.at(step) for step in ("sparkle_tools", "locked_venv", "build_app", "xcrun notarytool submit",
                                           "xcrun stapler staple", "generate_appcast", "sign_update --verify",
                                           f"git tag -a v{VERSION}", "gh release create")]
    assert order == sorted(order), release.calls


def test_the_tools_are_checked_again_after_the_build_before_they_are_given_the_key(release):
    """The build has SwiftPM at its packages, which is where the tools lie."""
    release.run()
    asked = [i for i, call in enumerate(release.calls) if call.startswith("sparkle_tools")]
    assert len(asked) == 2
    assert asked[0] < release.at("build_app") < asked[1] < release.at("generate_appcast")
    again = Release(release.where / "again")
    done = again.run(TOOLS_REFUSED_AT="2")
    assert done.returncode != 0 and "refusing to use it" in done.stderr
    assert again.called("build_app") and not again.called("generate_appcast") and not again.called("git tag")


def test_the_appcast_is_written_for_this_release_with_the_notes_inside_and_this_apps_key(release):
    release.run()
    written = release.called("generate_appcast")[0]
    assert "--account agent-grange-viz" in written, "this app's key, nobody else's"
    assert f"--download-url-prefix https://github.com/djp3/agent-grange-viz/releases/download/v{VERSION}/" in written
    assert "--embed-release-notes" in written and written.endswith("-o dist/appcast/appcast.xml dist/appcast")
    cast = release.root / "dist" / "appcast"
    assert (cast / f"agent-grange-viz-{VERSION}.md").read_text().startswith("A made-up release."), \
        "the notes sit beside the zip under its name, which is how generate_appcast pairs them"
    assert release.called("sign_update")[0] == "sign_update --verify --account agent-grange-viz dist/appcast/appcast.xml"


def test_a_dry_run_builds_and_writes_the_appcast_and_nothing_leaves_this_mac(release):
    done = release.run(dry=True)
    assert done.returncode == 0, done.stderr
    assert "dry run finished; nothing was published" in done.stdout and "dist/appcast/appcast.xml" in done.stdout
    assert release.called("build_app") and release.called("generate_appcast") and release.called("sign_update --verify")
    assert release.nothing_left_this_mac() and not release.called("git") and not release.called("gh")


# ---- what it refuses ----------------------------------------------------------------------------------
@pytest.mark.parametrize("fault, written", [
    ("the feed is not signed, only the zip", appcast(feed_signed=False)),
    ("the zip is not signed", appcast(zip_signed=False)),
    ("neither is signed", appcast(zip_signed=False, feed_signed=False)),
    ("the feed was changed after it was signed", appcast(covers=7)),
    ("the notes are not in it", appcast(notes=False)),
])
@pytest.mark.parametrize("dry", [False, True])
def test_nothing_is_tagged_or_published_unless_the_zip_and_the_feed_are_both_signed(release, fault, written, dry):
    """The first of these is what generate_appcast writes when the app does not ask for a
    signed feed. The script used to search for "edSignature", found the zip's, and went on."""
    release.feed.write_bytes(written)
    done = release.run(dry=dry)
    assert done.returncode == 1, fault
    assert "release: appcast.xml is not signed as the app requires" in done.stderr
    assert "dry run finished" not in done.stdout
    assert not (release.called("git tag") or release.called("git push") or release.called("gh release create")), fault


def test_a_feed_whose_signature_does_not_verify_is_not_published(release):
    done = release.run(VERIFIES="1")
    assert done.returncode == 1 and "the signature on appcast.xml does not verify" in done.stderr
    assert not (release.called("git tag") or release.called("git push") or release.called("gh release create"))


@pytest.mark.parametrize("says", ["ERROR! Access denied. Can't get keys from the keychain.",
                                  "ABORTED! You've cancelled the request to read the key from the Keychain.",
                                  "1097 bytes were expected to be signed, but actually read 1103 bytes."])
def test_why_the_appcast_did_not_verify_is_said_in_sign_updates_own_words(release, says):
    """It says why on standard output, which was thrown away: a keychain prompt that was
    refused, after the build and the notary, read as a signature that was wrong."""
    done = release.run(VERIFIES="1", SAYS=says)
    assert done.returncode == 1 and "sign_update said:" in done.stderr and says in done.stderr
    assert release.nothing_left_this_mac() or release.called("xcrun notarytool submit")   # notarized, not published
    assert not (release.called("git tag") or release.called("gh release create"))


def test_a_key_that_sign_update_could_not_read_is_never_repeated_in_what_is_shown(release):
    secret = "bm90IGEga2V5IGJ1dCBzZWNyZXQgYWxsIHRoZSBzYW1l"
    done = release.run(VERIFIES="1", SAYS=f"ERROR! Failed to decode base64 encoded key data from: {secret}")
    assert done.returncode == 1 and secret not in done.stderr + done.stdout
    assert "key data from: (not shown)" in done.stderr


@pytest.mark.parametrize("answer, said", [(ANOTHER_KEY, "is not the one in macos/Info.plist"),
                                           (None, "no Sparkle key in the login keychain")])
def test_a_key_that_is_not_the_apps_is_found_before_anything_is_built(release, answer, said):
    """The key is first used when the appcast is written: after the build, and after Apple's
    notary has had its minutes."""
    done = release.run(ANSWER=answer)
    assert done.returncode == 1 and said in done.stderr
    assert "release: the Sparkle key is not usable; nothing was built" in done.stderr
    assert not release.called("locked_venv") and not release.called("build_app") and release.nothing_left_this_mac()
    assert (release.where / "asked").read_text().splitlines() == ["--account agent-grange-viz -p"]


def test_a_key_file_named_from_where_the_script_was_called_is_still_found(release):
    """The script moves to the checkout before it does anything else."""
    (release.where / "exported.key").write_text("made up")
    done = release.run(dry=True, SPARKLE_PRIVATE_KEY_FILE="exported.key", FILE_HAS=THE_APPS_KEY)
    assert done.returncode == 0, done.stderr
    assert f"--ed-key-file {release.where / 'exported.key'}" in release.called("generate_appcast")[0]
    assert not (release.where / "asked").exists(), "the keychain is left alone"
    gone = release.run(dry=True, SPARKLE_PRIVATE_KEY_FILE="nowhere.key")
    assert gone.returncode == 1 and "names no file that can be read" in gone.stderr


def test_the_key_that_is_checked_is_the_key_that_signs(release):
    """Which key is decided once, in the release, and the check is handed that decision."""
    release.run(dry=True, SPARKLE_ACCOUNT="another-account")
    assert (release.where / "asked").read_text().splitlines() == ["--account another-account -p"]
    assert "--account another-account" in release.called("generate_appcast")[0]
    assert release.called("sign_update")[0].startswith("sign_update --verify --account another-account ")
    assert 'scripts/check_sparkle_key.sh "$TOOLS" "${SPARKLE_KEY_ARGS[@]}"' in (ROOT / "scripts" / "release.sh").read_text()


def test_a_key_on_standard_input_is_not_taken_for_a_file_named_dash(release):
    done = release.run(SPARKLE_PRIVATE_KEY_FILE="-")
    assert done.returncode == 1 and "must be a file" in done.stderr
    assert f"{release.where}/-" not in done.stderr and "names no file" not in done.stderr
    assert not release.called("build_app") and release.nothing_left_this_mac()


def test_a_key_file_whose_key_is_not_the_apps_is_found_before_anything_is_built(release):
    (release.where / "exported.key").write_text("made up")
    done = release.run(SPARKLE_PRIVATE_KEY_FILE="exported.key", FILE_HAS=ANOTHER_KEY)
    assert done.returncode == 1 and "is not the one in macos/Info.plist" in done.stderr
    assert not release.called("locked_venv") and not release.called("build_app") and release.nothing_left_this_mac()


def test_a_release_and_a_dry_run_of_one_build_the_copy_that_updates_itself(release):
    """Only that copy carries the feed: one built by hand does not, and so does not offer to
    replace itself with the published release."""
    release.run()
    assert release.called("build_app")[0].endswith("WITH_UPDATES=1")
    again = Release(release.where / "again")
    again.run(dry=True)
    assert again.called("build_app")[0].endswith("WITH_UPDATES=1")


@pytest.mark.parametrize("lost, said", [
    ("SUFeedURL", "does not carry the update feed: installed, it would never update again"),
    ("SURequireSignedFeed", "does not set SURequireSignedFeed"),
    ("SUVerifyUpdateBeforeExtraction", "does not set SUVerifyUpdateBeforeExtraction"),
    ("SUPublicEDKey", "does not carry the app's Sparkle key")])
@pytest.mark.parametrize("dry", [False, True])
def test_a_copy_that_would_not_update_itself_is_not_published(release, lost, said, dry):
    """Its appcast would be signed and verify all the same, so nothing after the build would
    stop it: and nobody who installed that copy would ever be offered another."""
    done = release.run(dry=dry, BUILD_LOSES=lost)
    assert done.returncode == 1 and said in done.stderr, lost
    assert release.called("build_app") and not release.called("generate_appcast")
    assert release.nothing_left_this_mac() and "dry run finished" not in done.stdout


def test_a_build_that_was_not_asked_for_the_copy_that_updates_is_caught(release):
    """Should the release ever stop saying it is one (WITH_UPDATES=1), the build leaves the
    feed out, as it does for a copy built by hand."""
    script = release.root / "scripts" / "release.sh"
    script.write_text(script.read_text().replace("WITH_UPDATES=1 PYTHON=", "PYTHON="))
    done = release.run()
    assert done.returncode == 1 and "does not carry the update feed" in done.stderr
    assert release.nothing_left_this_mac()


def test_without_notes_or_from_a_dirty_tree_nothing_is_built(release):
    done = release.run(DIRTY=" M main.py")
    assert done.returncode == 1 and "working tree is not clean" in done.stderr and not release.called("build_app")
    (release.root / "release-notes" / f"{VERSION}.md").unlink()
    done = release.run()
    assert done.returncode == 1 and f"no release notes in release-notes/{VERSION}.md" in done.stderr
    assert not release.called("build_app") and release.nothing_left_this_mac()


def test_a_keychain_that_is_still_talking_does_not_end_the_release(release):
    """Under `set -o pipefail` a pipeline fails when its reader leaves early and the writer is
    killed for writing to nobody. The identity was read with an awk that stopped at the first
    match: with more to come from `security`, the script ended there, with nothing said."""
    shim(release.root / "bin" / "security", SECURITY.rstrip("\n") + """
sleep 0.3
i=0; while [ $i -lt 2000 ]; do echo "     another line, after the one that was wanted"; i=$((i+1)); done
""")
    done = release.run(dry=True)
    assert done.returncode == 0, f"ended with {done.returncode}: {done.stderr[-300:]!r}"
    assert "identity: Developer ID Application: Made Up (TEAM123456)" in done.stdout


def test_when_the_tools_cannot_be_had_nothing_is_built(release):
    done = release.run(TOOLS_REFUSED_AT="1")
    assert done.returncode != 0 and "refusing to use it" in done.stderr
    assert not release.called("build_app") and release.nothing_left_this_mac()
