#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Check that nothing inside an assembled app needs a newer macOS than the app says it runs on.
#   scripts/check_macos_minimum.sh dist/agent-grange-viz.app          # the minimum from its Info.plist
#   scripts/check_macos_minimum.sh dist/agent-grange-viz.app 26.0     # or a minimum to try
# Every program and library records the oldest macOS it loads on (LC_BUILD_VERSION minos, or
# LC_VERSION_MIN_MACOSX on older ones). The Python side bundles Homebrew's libraries, which
# Homebrew builds for the macOS they were built on, so the app's minimum can rise unnoticed
# with a Homebrew upgrade. Exits 1 and names each file that needs more.
set -euo pipefail
APP=${1:?usage: check_macos_minimum.sh <app> [minimum]}
MIN=${2:-$(/usr/libexec/PlistBuddy -c "Print :LSMinimumSystemVersion" "$APP/Contents/Info.plist")}

# Whether version $1 is newer than version $2, comparing dotted numbers ("26.0" < "27.0").
newer() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]; }

checked=0 over=0
while IFS= read -r -d '' f; do
    file -b "$f" | grep -q "Mach-O" || continue
    # A universal file records one minimum per architecture: the highest one counts.
    needs=$(otool -l "$f" 2>/dev/null | awk '/LC_BUILD_VERSION|LC_VERSION_MIN_MACOSX/ {c=1}
                                         c && ($1=="minos" || $1=="version") {print $2; c=0}' | sort -V | tail -1)
    [ -n "$needs" ] || continue
    checked=$((checked + 1))
    if newer "$needs" "$MIN"; then
        echo "needs macOS $needs: ${f#"$APP"/}" >&2
        over=$((over + 1))
    fi
done < <(find "$APP" -type f -print0)

if [ "$over" -gt 0 ]; then
    echo "$over of $checked programs and libraries in $APP need a newer macOS than $MIN." >&2
    echo "Raise LSMinimumSystemVersion (macos/Info.plist) and the platform in macos/Package.swift," >&2
    echo "or build the Python side with libraries made for an older macOS." >&2
    exit 1
fi
echo "macOS $MIN or newer: all $checked programs and libraries in $APP load there"
