#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Print the folder that holds the tools of the Sparkle the app links against: its bin/ has
# generate_appcast, generate_keys and sign_update. They are checked before it is printed.
#
# They are the ones SwiftPM fetched together with the framework, for the version pinned in
# macos/Package.resolved: macos/.build/artifacts/.../bin. So the framework inside the app
# and the tools that sign for it are one download of one version, and nothing is fetched
# a second time from elsewhere. On a checkout that has not been built yet, the packages
# are resolved first, which is when SwiftPM downloads them.
#
# Each tool is checked against a pinned SHA-256 every time this runs, not only on the day
# it arrived: generate_appcast is handed the private EdDSA key every installed copy trusts,
# so it must be exactly the file that was reviewed, whatever has happened to the build
# folder since. Bumping Sparkle means updating macos/Package.resolved and the hashes below
# together; a version with no hashes here is refused.
set -euo pipefail
cd "$(dirname "$0")/.."

die() { echo "sparkle tools: $*" >&2; exit 1; }
TOOLS="generate_appcast generate_keys sign_update"

# /bin/bash on macOS is 3.2 (no associative arrays), so a case table.
pinned_sha256() {
    case "$1/$2" in
        2.10.1/generate_appcast) echo "cdc53c1da293c5f89648929678df2072f72a9f18d91744a06fcb8cc0e12a3219" ;;
        2.10.1/generate_keys)    echo "c37679f98d9121981cf7b2d18592fdc8b44931bffa604acf10c9e11791b6222e" ;;
        2.10.1/sign_update)      echo "43c249771bafc3aa581228abae00731a012d324691b8292860896635050be76b" ;;
        *) echo "" ;;
    esac
}

# The version actually linked is the one pinned in Package.resolved (Package.swift only gives
# the least). In that file a pin's version comes after its identity, so: the version after
# the line that names sparkle and before the next pin begins (a pin may have none, and its
# neighbour's is not it). Read with awk alone, so there is no interpreter to be missing.
[ -f macos/Package.resolved ] || die "macos/Package.resolved is missing: it says which Sparkle is linked"
VER=$(awk '/"identity"/ { if (found) exit; if ($0 ~ /:[[:space:]]*"sparkle"/) found = 1 }
           found && /"version"/ { gsub(/[^0-9.]/, ""); print; exit }' macos/Package.resolved)
[ -n "$VER" ] || die "macos/Package.resolved pins no version of Sparkle"
for tool in $TOOLS; do
    [ -n "$(pinned_sha256 "$VER" "$tool")" ] \
        || die "no pinned SHA-256 for $tool of Sparkle $VER in $0; add them alongside the version bump"
done

# Where SwiftPM put them. It keeps a binary package under .build/artifacts; the folders
# below that are its own business, so the tools are looked for, not assumed.
find_dir() {
    local found
    # -quit, not a pipe into head: a pipe that closes early fails the find, and under
    # `set -e` and pipefail that would end this script without a word.
    found=$(find macos/.build/artifacts -type f -name generate_appcast -path "*/bin/*" -print -quit \
            2>/dev/null || true)
    [ -n "$found" ] && dirname "$(dirname "$found")"
    return 0
}

# Which of the tools in a folder are not the pinned ones (missing counts), one per line.
wrong_in() {
    local tool
    for tool in $TOOLS; do
        [ -f "$1/bin/$tool" ] \
            && [ "$(shasum -a 256 "$1/bin/$tool" | cut -d' ' -f1)" = "$(pinned_sha256 "$VER" "$tool")" ] \
            || echo "$tool"
    done
}

DIR=$(find_dir)
if [ -z "$DIR" ] || [ -n "$(wrong_in "$DIR")" ]; then
    # Not built yet, or built for another version of Sparkle than is pinned now: have SwiftPM
    # fetch what Package.resolved says, and nothing newer.
    echo "resolving the Swift packages for Sparkle $VER's tools" >&2
    ( cd macos && swift package --only-use-versions-from-resolved-file resolve >&2 ) \
        || die "swift package resolve failed"
    DIR=$(find_dir)
fi
# (When only the artifacts folder was removed, SwiftPM still has it on record as fetched, and
# resolving fetches nothing: the record has to go too.)
[ -n "$DIR" ] || die "no Sparkle tools under macos/.build/artifacts, though the packages are resolved.
SwiftPM may believe it still has them: in macos/, run 'swift package reset' (or remove macos/.build), then try again"
WRONG=$(wrong_in "$DIR" | tr '\n' ' ')
[ -z "$WRONG" ] || die "not the pinned files of Sparkle $VER, refusing to use them: $WRONG(in $DIR/bin)"
for tool in $TOOLS; do
    [ -x "$DIR/bin/$tool" ] || die "$DIR/bin/$tool cannot be run"
done
echo "$PWD/$DIR"
