#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Sign an assembled agent-grange-viz.app inside-out.
#   scripts/sign_app.sh dist/agent-grange-viz.app "Developer ID Application: Name (TEAMID)"
#   scripts/sign_app.sh dist/agent-grange-viz.app -          # ad-hoc, for local builds
#   scripts/sign_app.sh dist/agent-grange-viz.app            # the same
set -euo pipefail
APP=${1:?usage: sign_app.sh <app> [identity]}
IDENTITY=${2:--}
cd "$(dirname "$0")/.."

# Developer ID builds get the hardened runtime (notarization requires it); Sparkle.framework
# and the Python tree are signed by the same team, so library validation passes, and no
# entitlement is asked for: nothing in the app needs executable memory or anyone else's
# libraries. An ad-hoc signature names nobody and carries no team, so an ad-hoc
# hardened-runtime app could not load its own frameworks: those builds are signed plainly
# instead. They run on the Mac that made them, and another Mac's Gatekeeper refuses them.
opts=(--force)
[ "$IDENTITY" != "-" ] && opts+=(--options runtime --timestamp)
sign() { codesign "${opts[@]}" --sign "$IDENTITY" "$@" 2> >(grep -v "replacing existing signature" >&2); }

CORE="$APP/Contents/Resources/agent-grange-viz-core"
FW="$APP/Contents/Frameworks/Sparkle.framework"

# 1. Every Mach-O in the Python tree: extension modules, dylibs, the interpreter library.
find "$CORE/_internal" -type f \( -name "*.so" -o -name "*.dylib" -o ! -name "*.*" \) | sort | while read -r f; do
    # Not `file | grep -q`: grep leaves at its first match, and under pipefail a `file` cut
    # off mid-answer (it says several lines of a universal binary) fails the test, which
    # would leave that binary unsigned without a word.
    kind=$(file -b "$f")
    case "$kind" in *Mach-O*) sign "$f" ;; esac
done
# The nested Python.framework is a bundle of its own (sign after its binary, before the app).
if [ -d "$CORE/_internal/Python.framework/Versions/Current" ]; then
    sign "$CORE/_internal/Python.framework"
fi
# 2. The entry executable.
sign "$CORE/agent-grange-viz-core"

# 3. Sparkle, the way its own instructions re-sign it (sparkle-project.org/documentation/sandboxing,
#    "Code Signing"): the helpers first, then the framework, and never --deep. Only the
#    downloader keeps the entitlements it ships with. Autoupdate ships with an application
#    identifier that names Sparkle's own project, which is not ours to vouch for.
sign "$FW/Versions/B/XPCServices/Installer.xpc"
sign --preserve-metadata=entitlements "$FW/Versions/B/XPCServices/Downloader.xpc"
sign "$FW/Versions/B/Autoupdate"
sign "$FW/Versions/B/Updater.app"
sign "$FW"

# 4. The app itself.
sign "$APP"
codesign --verify --deep --strict "$APP"
if [ "$IDENTITY" = "-" ]; then
    echo "signed $APP ad-hoc"
else
    echo "signed $APP with: $IDENTITY"
fi
