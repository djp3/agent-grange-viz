#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Is the key a release would sign with the one that installed copies trust?
#   scripts/check_sparkle_key.sh <tools>      # the folder scripts/sparkle_tools.sh prints
#
# scripts/release.sh asks this before it builds anything. The key is first used when the
# appcast is written, which is after the build and after Apple's notary has had its minutes:
# a key that is missing, or is not the app's, should not cost a whole run to find. (And there
# it would only leave the signature out: generate_appcast warns and goes on.)
#
# It reads what release.sh reads: SPARKLE_ACCOUNT (agent-grange-viz unless set), or
# SPARKLE_PRIVATE_KEY_FILE for a key exported to a file. The keychain's key is asked for by
# account, always: a login keychain can hold another project's Sparkle key under no account
# name, and that one is never this app's. Only the public half is read here, and it must be
# the app's SUPublicEDKey. For the keychain Sparkle's own generate_keys -p says it; for a
# file none of Sparkle's tools does, so scripts/sparkle_public_key.sh works it out.
set -euo pipefail
TOOLS=${1:?usage: check_sparkle_key.sh <tools folder>}
ACCOUNT=${SPARKLE_ACCOUNT:-agent-grange-viz}
cd "$(dirname "$0")/.."

WANT=$(/usr/libexec/PlistBuddy -c "Print :SUPublicEDKey" macos/Info.plist)
if [ -n "${SPARKLE_PRIVATE_KEY_FILE:-}" ]; then
    [ -f "$SPARKLE_PRIVATE_KEY_FILE" ] && [ -r "$SPARKLE_PRIVATE_KEY_FILE" ] \
        || { echo "SPARKLE_PRIVATE_KEY_FILE names no file that can be read: $SPARKLE_PRIVATE_KEY_FILE" >&2; exit 1; }
    HAVE=$(scripts/sparkle_public_key.sh "$SPARKLE_PRIVATE_KEY_FILE") \
        || { echo "$SPARKLE_PRIVATE_KEY_FILE does not hold a Sparkle key, as generate_keys -x exports one" >&2; exit 1; }
    [ "$HAVE" = "$WANT" ] \
        || { echo "the Sparkle key in $SPARKLE_PRIVATE_KEY_FILE is not the one in macos/Info.plist (SUPublicEDKey): installed copies would take nothing it signs" >&2; exit 1; }
    echo "sparkle key: the file $SPARKLE_PRIVATE_KEY_FILE, the one in Info.plist"
    exit 0
fi
HAVE=$("$TOOLS/bin/generate_keys" --account "$ACCOUNT" -p 2>/dev/null) \
    || { echo "no Sparkle key in the login keychain under the account '$ACCOUNT' (DEVELOPMENT.md, \"Releasing\")" >&2; exit 1; }
[ "$HAVE" = "$WANT" ] \
    || { echo "the Sparkle key under the account '$ACCOUNT' is not the one in macos/Info.plist (SUPublicEDKey): installed copies would take nothing it signs" >&2; exit 1; }
echo "sparkle key: the login keychain's, account $ACCOUNT, the one in Info.plist"
