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


def test_a_release_attaches_the_appcast_the_app_looks_for():
    release = read("scripts", "release.sh")
    assert f"REPO=${{GITHUB_REPO:-{REPO}}}" in release
    assert "SPARKLE_ACCOUNT=${SPARKLE_ACCOUNT:-agent-grange-viz}" in release, "this app's key, nobody else's"
    assert '--notes-file dist/release-notes.md "$ZIP" dist/appcast/appcast.xml' in release, \
        "the zip and the appcast go up together: releases/latest/download/appcast.xml is the feed"
    assert "--embed-release-notes" in release, "the notes travel inside the signed appcast"
    assert '--download-url-prefix "https://github.com/$REPO/releases/download/$TAG/"' in release


def test_nothing_is_tagged_before_the_appcast_is_signed():
    release = read("scripts", "release.sh")
    signed = release.index('die "appcast.xml carries no EdDSA signature"')
    assert release.index("generate_appcast\"") < signed < release.index('git tag -a "$TAG"')
    assert signed < release.index('if [ "$DRY_RUN" = 1 ]; then\n    echo\n'), "a dry run writes and checks it too"


def test_the_tools_that_are_handed_the_key_are_pinned_by_hash():
    pins = json.loads(read("macos", "Package.resolved"))["pins"]
    version = next(pin["state"]["version"] for pin in pins if pin["identity"] == "sparkle")
    fetch = read("scripts", "fetch_sparkle_tools.sh")
    assert re.search(rf'^\s*{re.escape(version)}\) echo "[0-9a-f]{{64}}" ;;$', fetch, re.M), \
        f"no pinned SHA-256 for Sparkle {version}, the version the app links"
    assert "shasum -a 256 -c --status" in fetch and fetch.index("shasum -a 256") < fetch.index("tar -xJf"), \
        "checked before anything is unpacked"
    package = read("macos", "Package.swift")
    assert "https://github.com/sparkle-project/Sparkle.git" in package
    assert "@executable_path/../Frameworks" in package, "where the app finds the framework it carries"


def test_sparkle_is_carried_checked_and_signed_inside_out():
    build = read("scripts", "build_app.sh")
    carried = build.index('cp -R "$SPARKLE_FW" "$APP/Contents/Frameworks/"')
    assert carried < build.index('scripts/check_min_macos.py "$APP"') < build.index('scripts/sign_app.sh "$APP"'), \
        "in the bundle before the macOS minimum is checked, and before anything is signed"
    sign = read("scripts", "sign_app.sh")
    order = [sign.index(part) for part in ("Downloader.xpc", "Installer.xpc", "Autoupdate", "Updater.app",
                                           'sign "$FW"', 'sign "$APP"')]
    assert order == sorted(order), "its helpers, then the framework, then the app"
    assert "sign --preserve-metadata=entitlements" in sign, "the helpers keep the entitlements they ship with"


def test_the_menu_offers_a_check_and_a_development_run_updates_nothing():
    swift = read("macos", "Sources", "AgentGrangeViz", "AppDelegate.swift")
    assert 'NSMenuItem(title: "Check for Updates…"' in swift
    assert "#selector(SPUStandardUpdaterController.checkForUpdates(_:))" in swift and "check.target = updater" in swift
    setup = swift[swift.index("private func setupUpdater()"):]
    assert setup.index("guard !isDevelopmentRun else { return }") < setup.index("SPUStandardUpdaterController(")
    assert swift.index("setupUpdater()") < swift.index("buildMenus()"), "the item is built with its target in hand"


def test_what_the_tools_leave_behind_is_not_committed():
    assert ".sparkle-tools/" in read(".gitignore").split()


def test_the_readme_says_what_the_update_check_is():
    readme = " ".join(read("README.md").split())
    assert "Check for Updates…" in readme and "once a day" in readme
    assert "does not update itself" not in readme
    development = " ".join(read("DEVELOPMENT.md").split())
    assert "generate_keys --account agent-grange-viz" in development and "there is no Sparkle" not in development
