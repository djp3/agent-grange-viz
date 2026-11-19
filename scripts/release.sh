#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Cut a release of the Mac app and publish it on GitHub.
#
#   scripts/release.sh               # build, notarize, publish
#   DRY_RUN=1 scripts/release.sh     # build with the Developer ID and zip into dist/, then stop:
#                                    # nothing is sent to Apple or GitHub, nothing is tagged
#
# Reads VERSION, builds dist/agent-grange-viz.app signed with a Developer ID from the
# packages in requirements.lock (a new venv, hash-checked: scripts/locked_venv.sh), notarizes
# and staples it, zips it, tags v<VERSION>, and creates the GitHub release with the zip
# attached and the notes in release-notes/<VERSION>.md.
#
# The app does not update itself (there is no Sparkle here, unlike agent-farm and
# agent-frolic), so a release is a download: nothing installed learns of it.
#
# The repository is public: the notes, like everything else in it, use made-up names.
#
# One-time setup:
#   - a "Developer ID Application" certificate in the login keychain
#   - notarization credentials: a notarytool keychain profile named agent-grange-viz
#       xcrun notarytool store-credentials agent-grange-viz --apple-id <id> --team-id <team> --password <app-specific>
#     (NOTARY_PROFILE=<name> uses a profile that already exists under another name), or
#     NOTARY_KEY_ID / NOTARY_ISSUER_ID / NOTARY_KEY_FILE (an App Store Connect API key)
#   - gh logged in with push access to the repo
#
# Each release:
#   echo 0.2.0 > VERSION
#   $EDITOR release-notes/0.2.0.md      # what is new, written for people who use the app
#   git add VERSION release-notes && git commit -m "Release 0.2.0"
#   scripts/release.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(tr -d '[:space:]' < VERSION)
TAG="v$VERSION"
REPO=${GITHUB_REPO:-djp3/agent-grange-viz}
NOTARY_PROFILE=${NOTARY_PROFILE:-agent-grange-viz}
DRY_RUN=${DRY_RUN:-0}
APP=dist/agent-grange-viz.app
ZIP=dist/agent-grange-viz-$VERSION.zip
NOTES=release-notes/$VERSION.md

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { echo "release: $*" >&2; exit 1; }

step "preflight $TAG"
# Sign by certificate hash rather than by name: a keychain can hold two certificates with
# the same name (a renewed one beside the old), and codesign rejects an ambiguous name.
IDENTITY=${CODESIGN_IDENTITY:-$(security find-identity -v -p codesigning | awk '/Developer ID Application/ {print $2; exit}')}
[ -n "$IDENTITY" ] || die "no 'Developer ID Application' identity in the keychain (or set CODESIGN_IDENTITY)"
IDENTITY_NAME=$(security find-identity -v -p codesigning | awk -F'"' -v h="$IDENTITY" '$0 ~ h {print $2; exit}')
[ -s "$NOTES" ] || die "no release notes in $NOTES; write what is new in $VERSION first"
NOTES_SUFFIX=""
if [ "$DRY_RUN" = 1 ]; then
    echo "dry run: nothing will be notarized, tagged or published"
else
    DIRTY=$(git status --porcelain)       # includes untracked files, which PyInstaller would bundle
    if [ -n "$DIRTY" ]; then
        [ "${ALLOW_DIRTY:-0}" = 1 ] || die "working tree is not clean (ALLOW_DIRTY=1 overrides):
$DIRTY"
        echo "WARNING: building from a dirty tree; the release notes will say so" >&2
        NOTES_SUFFIX="

> Built from a dirty working tree (ALLOW_DIRTY=1); the tag may not reproduce this build."
    fi
    git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && die "tag $TAG already exists locally; bump VERSION, or delete it if the previous attempt did not publish"
    git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1 && die "tag $TAG already exists on origin; bump VERSION first"
    gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1 && die "release $TAG already exists on GitHub"
    if [ -n "${NOTARY_KEY_FILE:-}" ]; then
        NOTARY_ARGS=(--key "$NOTARY_KEY_FILE" --key-id "${NOTARY_KEY_ID:?NOTARY_KEY_ID}" --issuer "${NOTARY_ISSUER_ID:?NOTARY_ISSUER_ID}")
    else
        xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1 \
            || die "no notarytool keychain profile '$NOTARY_PROFILE'; create it with: xcrun notarytool store-credentials $NOTARY_PROFILE ...
(or name one that exists: NOTARY_PROFILE=<name> scripts/release.sh)"
        NOTARY_ARGS=(--keychain-profile "$NOTARY_PROFILE")
    fi
fi
echo "identity: ${IDENTITY_NAME:-$IDENTITY} ($IDENTITY)"

step "locked packages"
# The app is built from a new venv holding exactly requirements.lock, every file checked
# against its hash, never from .venv: whatever is installed there by hand would ship.
PYTHON=$(scripts/locked_venv.sh build/release-venv)
echo "built from requirements.lock: $PYTHON"

step "build"
# A release always rebuilds the Python side: SKIP_PYTHON=1 left over in the shell must not
# ship an older build of it. A dry run may reuse it.
[ "$DRY_RUN" = 1 ] || export SKIP_PYTHON=0
PYTHON="$PYTHON" CODESIGN_IDENTITY="$IDENTITY" scripts/build_app.sh

if [ "$DRY_RUN" = 1 ]; then
    step "notarize: skipped (dry run)"
else
    step "notarize"
    rm -f dist/notarize.zip
    ditto -c -k --keepParent "$APP" dist/notarize.zip
    xcrun notarytool submit dist/notarize.zip "${NOTARY_ARGS[@]}" --wait
    xcrun stapler staple "$APP"
    rm -f dist/notarize.zip
    spctl --assess --type execute --verbose=2 "$APP"
fi

step "archive"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"
{ cat "$NOTES"; printf '%s\n' "$NOTES_SUFFIX"; } > dist/release-notes.md

if [ "$DRY_RUN" = 1 ]; then
    echo
    echo "dry run finished; nothing was published. What a release would attach:"
    echo "  $ZIP"
    exit 0
fi

step "publish $TAG"
git push origin HEAD                      # the release must be reproducible from the repo
# The tag is created last and removed again if publishing fails, so a re-run starts clean.
git tag -a "$TAG" -m "agent-grange-viz $VERSION"
if ! { git push origin "$TAG" || { sleep 5; git push origin "$TAG"; }; }; then   # GitHub occasionally rejects a fresh tag once
    git tag -d "$TAG" >/dev/null
    die "pushing tag $TAG failed; nothing was published, re-run to try again"
fi
# --generate-notes adds GitHub's "Full Changelog" link after the notes.
if ! gh release create "$TAG" --repo "$REPO" --title "agent-grange-viz $VERSION" --generate-notes \
        --notes-file dist/release-notes.md "$ZIP"; then
    git push --delete origin "$TAG" >/dev/null 2>&1 || true
    git tag -d "$TAG" >/dev/null
    die "creating the GitHub release failed; the tag was removed again, re-run to try again"
fi
echo
echo "released: https://github.com/$REPO/releases/tag/$TAG"
