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
from pathlib import Path

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
    script = read("scripts", "sparkle_tools.sh")
    for tool in ("generate_appcast", "generate_keys", "sign_update"):
        assert re.search(rf'^\s*{re.escape(version)}:{tool}\)\s+echo "[0-9a-f]{{64}}" ;;$', script, re.M), \
            f"no pinned SHA-256 for the {tool} of Sparkle {version}, the version the app links"
    # that the script acts on them is tests/test_sparkle_tools.py's to show: it runs it
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


def test_sparkles_license_goes_wherever_its_framework_goes():
    """Its license asks that every copy carry its notice. The app only named it."""
    build = read("scripts", "build_app.sh")
    copied = build.index('cp "$SPARKLE_LICENSE" "$APP/Contents/Resources/LICENSE-Sparkle"')
    refused = build.index('[ -s "$SPARKLE_LICENSE" ] || {')
    assert refused < copied < build.index('scripts/sign_app.sh "$APP"'), "there, or no build; and sealed by the signature"
    assert "LICENSE-Sparkle" in read("NOTICE")
    built = ROOT / "dist" / "agent-grange-viz.app" / "Contents"
    if (built / "Frameworks" / "Sparkle.framework").is_dir():          # an app was built here: look at it
        assert "Permission is hereby granted" in (built / "Resources" / "LICENSE-Sparkle").read_text()


def test_the_menu_offers_a_check_and_only_a_copy_with_a_feed_updates_itself():
    swift = read("macos", "Sources", "AgentGrangeViz", "AppDelegate.swift")
    assert 'NSMenuItem(title: "Check for Updates…"' in swift
    assert "#selector(SPUStandardUpdaterController.checkForUpdates(_:))" in swift and "check.target = updater" in swift
    assert 'updatesItself: Bool { Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") != nil }' in swift
    setup = swift[swift.index("private func setupUpdater()"):]
    assert setup.index("guard updatesItself else { return }") < setup.index("SPUStandardUpdaterController(")
    assert swift.index("setupUpdater()") < swift.index("buildMenus()"), "the item is built with its target in hand"


def test_a_local_build_is_made_without_the_feed():
    """It has the installed copy's bundle id, and so its Sparkle preferences. With automatic
    installs on, a build from a branch would be replaced by the published app as it quit."""
    build = read("scripts", "build_app.sh")
    local = build[build.index('if [ "${CODESIGN_IDENTITY:--}" = "-" ]; then'):]
    local = local[:local.index("\nfi\n")]
    assert '/usr/libexec/PlistBuddy -c "Delete :SUFeedURL" "$APP/Contents/Info.plist"' in local
    written = build.index('sed "s/__VERSION__/$VERSION/g" macos/Info.plist > "$APP/Contents/Info.plist"')
    assert written < build.index(local) < build.index('scripts/sign_app.sh "$APP"'), "after it is written, before it is signed"
    assert "SUFeedURL" in read("macos", "Info.plist"), "a release, built from the same file, keeps it"


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
