#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Download the Sparkle release the app links against (generate_appcast, generate_keys,
# sign_update) into .sparkle-tools/<version>/ and print that directory.
#
# generate_appcast is later handed the private EdDSA key every installed copy trusts, so
# the tools must be exactly the files that were reviewed, each time they are used:
#   - the tarball is verified against a pinned SHA-256 before anything is unpacked;
#   - it is unpacked beside its place and moved there whole, so an unpacking that was cut
#     short leaves nothing that looks like tools;
#   - on every run, downloaded just now or months ago, each tool is verified against its own
#     pinned SHA-256. .sparkle-tools/ is ignored by git, so nothing else would notice a tool
#     that was changed or damaged where it lies.
# Bumping Sparkle means updating the version in macos/Package.swift, the pin in
# macos/Package.resolved, and the hashes below together.
set -euo pipefail
cd "$(dirname "$0")/.."

TOOLS="generate_appcast generate_keys sign_update"

# /bin/bash on macOS is 3.2 (no associative arrays), so a case table: <version>:<what>, where
# <what> is the release's tarball or one of the tools in its bin/.
pinned_sha256() {
    case "$1:$2" in
        2.10.1:tarball)          echo "1a856d2a9edb8e628de88121d7be04cf0e2167f8641650b78fca00b14244cf27" ;;
        2.10.1:generate_appcast) echo "cdc53c1da293c5f89648929678df2072f72a9f18d91744a06fcb8cc0e12a3219" ;;
        2.10.1:generate_keys)    echo "c37679f98d9121981cf7b2d18592fdc8b44931bffa604acf10c9e11791b6222e" ;;
        2.10.1:sign_update)      echo "43c249771bafc3aa581228abae00731a012d324691b8292860896635050be76b" ;;
        *) echo "" ;;
    esac
}

# The version actually linked is the one pinned in Package.resolved; Package.swift only
# gives the minimum.
resolved_version() {
    python3 - <<'PY' 2>/dev/null
import json
for pin in json.load(open("macos/Package.resolved"))["pins"]:
    if pin["identity"] == "sparkle":
        print(pin["state"]["version"]); break
PY
}
VER=${SPARKLE_VERSION:-$(resolved_version)}
[ -n "$VER" ] || VER=$(sed -n 's/.*sparkle-project\/Sparkle.git", from: "\([0-9.]*\)".*/\1/p' macos/Package.swift)
[ -n "$VER" ] || { echo "could not determine the Sparkle version" >&2; exit 1; }
SHA=$(pinned_sha256 "$VER" tarball)
[ -n "$SHA" ] || { echo "no pinned SHA-256 for Sparkle $VER in $0; add it alongside the version bump" >&2; exit 1; }

DIR=.sparkle-tools/$VER
if [ ! -d "$DIR" ]; then
    echo "fetching Sparkle $VER tools" >&2
    TMP=$(mktemp -d)
    NEW=$DIR.unpacking.$$
    trap 'rm -rf "$TMP" "$NEW"' EXIT
    curl -fsSL "https://github.com/sparkle-project/Sparkle/releases/download/$VER/Sparkle-$VER.tar.xz" -o "$TMP/sparkle.tar.xz"
    echo "$SHA  $TMP/sparkle.tar.xz" | shasum -a 256 -c --status \
        || { echo "Sparkle-$VER.tar.xz does not match the pinned SHA-256; refusing to unpack it" >&2; exit 1; }
    mkdir -p "$NEW"
    tar -xJf "$TMP/sparkle.tar.xz" -C "$NEW"
    mv "$NEW" "$DIR"                      # whole, or not at all
fi

for tool in $TOOLS; do
    WANT=$(pinned_sha256 "$VER" "$tool")
    [ -n "$WANT" ] || { echo "no pinned SHA-256 for Sparkle $VER's $tool in $0" >&2; exit 1; }
    [ -f "$DIR/bin/$tool" ] || { echo "$DIR/bin/$tool is missing; delete $DIR and run this again" >&2; exit 1; }
    echo "$WANT  $DIR/bin/$tool" | shasum -a 256 -c --status \
        || { echo "$DIR/bin/$tool is not the $tool of Sparkle $VER (its SHA-256 differs from the pinned one); refusing to use it. Delete $DIR to fetch it again" >&2; exit 1; }
done
echo "$PWD/$DIR"
