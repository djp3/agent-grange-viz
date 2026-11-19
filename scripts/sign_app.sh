#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Sign an assembled agent-grange-viz.app inside-out, ad-hoc: for a build that runs on the
# Mac it was made on.
#   scripts/sign_app.sh dist/agent-grange-viz.app
#
# An ad-hoc signature names nobody. It is enough for macOS to run the app where it was
# built (Apple silicon runs nothing unsigned), and not enough to hand the app to anyone
# else: another Mac's Gatekeeper refuses it. A copy to give away needs what agent-frolic's
# scripts/sign_app.sh and scripts/release.sh do and this does not yet: a Developer ID
# Application certificate, the hardened runtime (--options runtime --timestamp, and for the
# Python entry executable an entitlements file if anything in it then needs executable
# memory), and Apple's notarization.
set -euo pipefail
APP=${1:?usage: sign_app.sh <app>}
cd "$(dirname "$0")/.."

# Plainly, with no hardened runtime: an ad-hoc signature carries no team, so an ad-hoc
# hardened-runtime app could not load its own libraries.
sign() { codesign --force --sign - "$@" 2> >(grep -v "replacing existing signature" >&2); }

CORE="$APP/Contents/Resources/agent-grange-viz-core"

# 1. Every Mach-O in the Python tree: extension modules, dylibs, the interpreter library.
find "$CORE/_internal" -type f \( -name "*.so" -o -name "*.dylib" -o ! -name "*.*" \) | sort | while read -r f; do
    if file -b "$f" | grep -q "Mach-O"; then sign "$f"; fi
done
# The nested Python.framework is a bundle of its own (sign after its binary, before the app).
if [ -d "$CORE/_internal/Python.framework/Versions/Current" ]; then
    sign "$CORE/_internal/Python.framework"
fi
# 2. The entry executable.
sign "$CORE/agent-grange-viz-core"

# 3. The app itself.
sign "$APP"
codesign --verify --deep --strict "$APP"
echo "signed $APP ad-hoc"
