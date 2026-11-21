# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Mac app updates itself with Sparkle, the way agent-farm and agent-frolic do. Nothing
here runs Sparkle. These hold its pieces to each other: the feed the app polls is the file a
release attaches, the key it trusts is a key, the tools that are handed the private half are
pinned by hash, and the framework is signed before the app that carries it."""
import base64
import json
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPO = "djp3/agent-grange-viz"


def read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text()


def info() -> dict:
    with open(ROOT / "macos" / "Info.plist", "rb") as f:
        return plistlib.load(f)


def test_the_app_knows_where_to_look_and_whom_to_trust():
    plist = info()
    assert plist["SUFeedURL"] == f"https://github.com/{REPO}/releases/latest/download/appcast.xml"
    assert plist["SUEnableAutomaticChecks"] is True, "it checks without first asking whether it may"
    assert plist["SUScheduledCheckInterval"] == 86400, "once a day"
    key = base64.b64decode(plist["SUPublicEDKey"], validate=True)
    assert len(key) == 32, "an Ed25519 public key"
    assert "SUEnableSystemProfiling" not in plist, "nothing about the Mac is sent with the check"


def test_the_app_takes_no_feed_that_is_not_signed():
    """Without SURequireSignedFeed only the zip is signed: whoever could change appcast.xml
    could rewrite the notes or hide an update, and the app would believe it. Sparkle will not
    start with that key set and SUVerifyUpdateBeforeExtraction missing."""
    plist = info()
    assert plist["SURequireSignedFeed"] is True
    assert plist["SUVerifyUpdateBeforeExtraction"] is True


# What a release does with these, and what it refuses, is tests/test_release.py's to show: it
# runs scripts/release.sh.


def test_the_tools_that_are_handed_the_key_are_pinned_by_hash():
    pins = json.loads(read("macos", "Package.resolved"))["pins"]
    version = next(pin["state"]["version"] for pin in pins if pin["identity"] == "sparkle")
    package = read("macos", "Package.swift")
    assert "https://github.com/sparkle-project/Sparkle.git" in package
    # The manifest's minimum is never above the pin: then every check here would read a pin
    # the build could not use, and SwiftPM, left to itself, would move it during a release.
    least = re.search(r'sparkle-project/Sparkle\.git", from: "([0-9.]+)"', package).group(1)
    assert tuple(map(int, least.split("."))) <= tuple(map(int, version.split("."))), \
        f"macos/Package.swift asks for Sparkle {least} or later and macos/Package.resolved pins {version}"
    build = read("scripts", "build_app.sh")
    assert "swift build --only-use-versions-from-resolved-file" in build, "the build does not move the pin"
    assert "@executable_path/../Frameworks" in package, "where the app finds the framework it carries"


def test_sparkle_is_carried_checked_and_signed_inside_out():
    build = read("scripts", "build_app.sh")
    carried = build.index('cp -R "$SPARKLE_FW" "$APP/Contents/Frameworks/"')
    assert carried < build.index('scripts/check_min_macos.py "$APP"') < build.index('scripts/sign_app.sh "$APP"'), \
        "in the bundle before the macOS minimum is checked, and before anything is signed"
    # The one place the app looks for the framework is the one it is copied to, or no build.
    assert 'NEEDED="@executable_path/../Frameworks"' in build
    assert '[ "$(rpaths)" = "$NEEDED" ] || {' in build and build.index('[ "$(rpaths)" = "$NEEDED" ]') < carried
    sign = read("scripts", "sign_app.sh")
    steps = [line.strip() for line in sign.splitlines() if line.startswith("sign ")]
    sparkle = [step for step in steps if "$FW" in step]
    assert sparkle == ['sign "$FW/Versions/B/XPCServices/Installer.xpc"',
                       'sign --preserve-metadata=entitlements "$FW/Versions/B/XPCServices/Downloader.xpc"',
                       'sign "$FW/Versions/B/Autoupdate"',
                       'sign "$FW/Versions/B/Updater.app"',
                       'sign "$FW"'], "Sparkle's own recipe: only the downloader keeps what it ships with"
    assert steps.index('sign "$FW"') < steps.index('sign "$APP"'), "the framework before the app that carries it"
    assert "--deep" not in " ".join(steps)


def test_the_licenses_of_what_the_swift_host_links_go_into_the_app():
    """Sparkle's framework is carried whole and SwiftTerm is linked in. Each license asks that
    its text go with every copy; the app only named them."""
    build = read("scripts", "build_app.sh")
    assert "for package in Sparkle SwiftTerm; do" in build
    copied = build.index('cp "macos/.build/checkouts/$package/LICENSE" "$APP/Contents/Resources/LICENSE-$package"')
    refused = build.index('[ -s "macos/.build/checkouts/$package/LICENSE" ]')
    assert refused < copied < build.index('scripts/sign_app.sh "$APP"'), "there, or no build; and sealed by the signature"
    notice = read("NOTICE")
    assert "LICENSE-Sparkle" in notice and "LICENSE-SwiftTerm" in notice
    built = ROOT / "dist" / "agent-grange-viz.app" / "Contents"
    if (built / "Resources" / "LICENSE-SwiftTerm").is_file():           # an app was built here since: look at it
        for package in ("Sparkle", "SwiftTerm"):
            assert "Permission is hereby granted" in (built / "Resources" / f"LICENSE-{package}").read_text()


def test_the_menu_offers_a_check_and_only_a_copy_with_a_feed_updates_itself():
    swift = read("macos", "Sources", "AgentGrangeViz", "AppDelegate.swift")
    assert 'NSMenuItem(title: "Check for Updates…"' in swift
    assert "#selector(SPUStandardUpdaterController.checkForUpdates(_:))" in swift and "check.target = updater" in swift
    assert 'updatesItself: Bool { Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") != nil }' in swift
    setup = swift[swift.index("private func setupUpdater()"):]
    assert setup.index("guard updatesItself else { return }") < setup.index("SPUStandardUpdaterController(")
    assert swift.index("setupUpdater()") < swift.index("buildMenus()"), "the item is built with its target in hand"


def test_only_a_release_carries_the_feed_however_a_copy_is_signed():
    """A copy built by hand has the installed copy's bundle id, and so its Sparkle preferences.
    With automatic installs on, a build from a branch would be replaced by the published app
    as it quit. A Developer ID does not make a copy a release: release.sh says which it is."""
    build = read("scripts", "build_app.sh")
    local = build[build.index('if [ "${WITH_UPDATES:-0}" != 1 ]; then'):]
    local = local[:local.index("\nfi\n")]
    for key in ("SUFeedURL", "SUEnableAutomaticChecks", "SUScheduledCheckInterval"):
        assert f'-c "Delete :{key}"' in local, key
    assert "CODESIGN_IDENTITY" not in local, "how it is signed does not decide it"
    written = build.index('sed "s/__VERSION__/$VERSION/g" macos/Info.plist > "$APP/Contents/Info.plist"')
    assert written < build.index(local) < build.index('scripts/sign_app.sh "$APP"'), "after it is written, before it is signed"
    assert "SUFeedURL" in read("macos", "Info.plist"), "a release, built from the same file, keeps it"
    # that a release, and a dry run of one, ask for the copy that updates itself: tests/test_release.py


def test_closing_the_viewers_window_quits_whatever_else_is_open():
    """Sparkle's update window is a window too. With it open, closing the viewer's window left
    the app running with nothing to show: the app only quit after its last window. (Seen on a
    scratch copy offered a made-up update.)"""
    swift = read("macos", "Sources", "AgentGrangeViz", "AppDelegate.swift")
    assert "w.delegate = self" in swift, "the app hears of its window closing"
    closing = swift[swift.index("func windowWillClose("):]
    closing = closing[:closing.index("\n    }\n")]
    assert "=== window" in closing, "the viewer's window, not one of Sparkle's"
    assert "NSApp.terminate(nil)" in closing
    assert "w.isReleasedWhenClosed = false" in swift, "nor is the window freed while the app still holds it"


def test_the_readme_says_what_the_update_check_is():
    readme = " ".join(read("README.md").split())
    assert "Check for Updates…" in readme and "once a day" in readme
    assert "does not update itself" not in readme
    development = " ".join(read("DEVELOPMENT.md").split())
    assert "generate_keys --account agent-grange-viz" in development and "there is no Sparkle" not in development


# ---- the one place the app looks for its frameworks ---------------------------------------------
NEEDED = "@executable_path/../Frameworks"


def rpath_step() -> str:
    """build_app.sh's own lines that keep the one rpath, from rpaths() to the check after it."""
    build = read("scripts", "build_app.sh").splitlines()
    first = next(i for i, line in enumerate(build) if line.startswith("rpaths() {"))
    last = next(i for i, line in enumerate(build) if line.startswith('[ "$(rpaths)" = "$NEEDED" ]'))
    return "\n".join(build[first:last + 1])


def executable(tmp_path, *rpaths: str) -> Path:
    """A tiny program that looks for its libraries in `rpaths`."""
    if not shutil.which("cc"):
        pytest.skip("no C compiler to make an executable with rpaths")
    exe = tmp_path / "exe"
    subprocess.run(["cc", "-x", "c", "-", "-o", str(exe), *[f"-Wl,-rpath,{rp}" for rp in rpaths]],
                   input=b"int main(void){return 0;}\n", check=True)
    return exe


def keep_one_rpath(exe: Path) -> subprocess.CompletedProcess:
    script = f'set -euo pipefail\nEXE="{exe}"\nNEEDED="{NEEDED}"\n{rpath_step()}\n'
    return subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True)


def rpaths_of(exe: Path) -> list:
    lines = subprocess.run(["otool", "-l", str(exe)], capture_output=True, text=True, check=True).stdout.splitlines()
    return [lines[i + 2].split("path ", 1)[1].rsplit(" (offset", 1)[0]
            for i, line in enumerate(lines) if line.strip() == "cmd LC_RPATH"]


def test_an_rpath_with_a_space_in_it_is_taken_out_whole(tmp_path):
    """otool prints "path /tmp/My Checkout/... (offset 12)". awk's $2 was "/tmp/My", which
    install_name_tool could not find, and the build stopped: a checkout in a folder with a
    space in its name could not be built, once SwiftPM records an absolute rpath."""
    spaced = str(tmp_path / "My Checkout" / "macos" / ".build" / "release")
    exe = executable(tmp_path, "@loader_path", spaced, NEEDED)
    assert spaced in rpaths_of(exe)
    done = keep_one_rpath(exe)
    assert done.returncode == 0, done.stderr
    assert rpaths_of(exe) == [NEEDED]


def test_the_usual_two_rpaths_come_down_to_the_one(tmp_path):
    exe = executable(tmp_path, "@loader_path", NEEDED)
    done = keep_one_rpath(exe)
    assert done.returncode == 0, done.stderr
    assert rpaths_of(exe) == [NEEDED]


def test_without_the_rpath_it_needs_the_build_stops_and_says_where_it_looked(tmp_path):
    spaced = str(tmp_path / "My Checkout")
    exe = executable(tmp_path, "@loader_path", spaced)
    done = keep_one_rpath(exe)
    assert done.returncode == 1
    assert f"must look for its frameworks in {NEEDED} alone" in done.stderr
