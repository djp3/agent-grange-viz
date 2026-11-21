#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Build dist/agent-grange-viz.app from a checkout:
#   1. PyInstaller onedir of main.py            -> dist/python/agent-grange-viz-core
#   2. SwiftPM release build of the host app     -> macos/.build/release/AgentGrangeViz
#   3. assemble the bundle (binary, Sparkle.framework, the Python tree, icon, Info.plist),
#      and check the macOS minimum (scripts/check_min_macos.py): stop if anything in it needs
#      a newer macOS than Info.plist says, or the Swift executable records another minimum
#   4. sign it: ad-hoc, or with CODESIGN_IDENTITY="Developer ID Application: …" (or that
#      certificate's hash) and the hardened runtime, as scripts/release.sh does
#   5. ask the signed copy to check itself, once main.py knows how (--selfcheck)
# Needs: .venv with requirements.txt + requirements-build.txt (or PYTHON=<the python of
# another venv, such as the one scripts/locked_venv.sh makes>), Xcode command line tools.
# The version comes from VERSION and is used for both CFBundleShortVersionString and
# CFBundleVersion (Sparkle compares the latter; dotted numbers compare naturally).
#
# By itself this builds an app for the Mac it is built on. scripts/release.sh builds the one
# that leaves it: signed with a Developer ID, notarized, and published where Sparkle looks.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(tr -d '[:space:]' < VERSION)
PY=${PYTHON:-./.venv/bin/python3}
APP=dist/agent-grange-viz.app
CORE=agent-grange-viz-core

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# The app this builds opens only on the macOS that macos/Info.plist names, or a later one. On
# an earlier macOS say so before building anything, and say what does work there.
# (MACOS_VERSION= stands in for this Mac's, for the tests.)
NEEDS=$(/usr/libexec/PlistBuddy -c "Print :LSMinimumSystemVersion" macos/Info.plist)
HAS=${MACOS_VERSION:-$(sw_vers -productVersion)}
if [ "$(printf '%s\n%s\n' "$NEEDS" "$HAS" | sort -V | head -1)" != "$NEEDS" ]; then
    printf '%s\n' \
        "agent-grange-viz.app needs macOS ${NEEDS%.0} or later, and this Mac runs macOS $HAS." \
        "" \
        "macOS would refuse to open the app here, so nothing was built." \
        "" \
        "The viewer itself does not need macOS ${NEEDS%.0}. Run it in a terminal instead:" \
        "    .venv/bin/python main.py" \
        "See \"On macOS 26 (Tahoe) or earlier?\" in README.md." >&2
    exit 1
fi

if [ "${SKIP_PYTHON:-0}" = 1 ] && [ -x dist/python/$CORE/$CORE ]; then
    step "python core: reusing dist/python/$CORE (SKIP_PYTHON=1)"
else
    step "python core: PyInstaller onedir ($($PY --version))"
    $PY -m PyInstaller --noconfirm --clean --log-level WARN \
        --distpath dist/python --workpath build/pyinstaller packaging/$CORE.spec
fi

step "host app: swift build"
BIN=macos/.build/release/AgentGrangeViz
rm -f "$BIN"                                   # so an existing binary can only come from this build
# Held to Package.resolved: left to itself SwiftPM moves a pin that the manifest has outgrown
# and rewrites the file, mid-build, and the app would link a version no commit names.
( cd macos && swift build --only-use-versions-from-resolved-file -c release --product AgentGrangeViz 2>&1 \
    | grep -E "error|warning: unre|Build complete"
  [ "${PIPESTATUS[0]}" -eq 0 ] )              # keep the compiler's verdict, not grep's
[ -x "$BIN" ] || { echo "swift build did not produce $BIN" >&2; exit 1; }
# Sparkle comes as a binary framework that SwiftPM downloads next to the build.
SPARKLE_FW=$(find macos/.build/artifacts -type d -name Sparkle.framework -path "*macos-*" | head -1)
[ -d "$SPARKLE_FW" ] || { echo "Sparkle.framework not found under macos/.build/artifacts" >&2; exit 1; }

step "icon"
# Made afresh by every build, which takes a second. Git ignores the .icns, so one left by an
# earlier build or made by hand could otherwise go into an app that the commit it was built
# from cannot make again. Going by which file is newer is not enough: a copied or restored
# file keeps an old date.
ICNS=macos/Resources/AppIcon.icns
ICON=macos/Resources/icon.png              # the drawing at 1024 px
ICON_SMALL=macos/Resources/icon-small.png  # three granges alone, for the 16 and 32 px sizes
scripts/render_icon.sh --check             # the two pictures are the drawings as they stand, or stop
scripts/make_icon.sh "$ICON" "$ICON_SMALL" "$ICNS"

step "assemble $APP ($VERSION)"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Frameworks" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/AgentGrangeViz"
# SwiftPM records an absolute rpath into .build for the Sparkle artifact; the app only
# needs the @executable_path/../Frameworks one set in Package.swift.
otool -l "$APP/Contents/MacOS/AgentGrangeViz" | awk '/LC_RPATH/{f=1} f&&/ path /{print $2; f=0}' | while read -r rp; do
    case "$rp" in /*) install_name_tool -delete_rpath "$rp" "$APP/Contents/MacOS/AgentGrangeViz" 2>/dev/null || true ;; esac
done
cp -R "$SPARKLE_FW" "$APP/Contents/Frameworks/"
cp -R dist/python/$CORE "$APP/Contents/Resources/$CORE"
cp "$ICNS" "$APP/Contents/Resources/AppIcon.icns"
# The license the code is under, and the notice that says so and where the source is, go in
# every copy of the app: the license asks for both. The icon is this project's own drawing,
# so there are no credits to carry beside them.
cp LICENSE NOTICE "$APP/Contents/Resources/"
sed "s/__VERSION__/$VERSION/g" macos/Info.plist > "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

step "macOS minimum"
# Nothing inside may need a newer macOS than Info.plist promises (Homebrew's libraries follow
# the macOS they were built on), or the app would install there and fail to start. And the
# Swift executable must record that same minimum: it is said twice, in Info.plist and in
# Package.swift, and one raised without the other would pass otherwise. The check runs by
# itself: it needs no package, so a build that reuses the Python core (SKIP_PYTHON=1) needs
# no venv for it either.
scripts/check_min_macos.py "$APP"

step "sign"
scripts/sign_app.sh "$APP" "${CODESIGN_IDENTITY:--}"

step "self-check of the signed copy"
# The packaged copy proves it is whole by starting without a screen and drawing once, with
# no grange and no Docker asked. That is main.py's to do (`--selfcheck`, as agent-frolic's
# has); until it can, the build says so instead of starting a viewer that would wait for a
# terminal it does not have.
if grep -q -e '--selfcheck' main.py; then
    AGENT_GRANGE_VIZ_VERSION="$VERSION" "$APP/Contents/Resources/$CORE/$CORE" --selfcheck
else
    echo "skipped: main.py has no --selfcheck yet"
fi

echo
echo "built $APP  version $VERSION  ($(du -sh "$APP" | cut -f1))"
