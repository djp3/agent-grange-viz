#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Render the icon's drawings to the pictures the build uses, and check that they still match.
#     usage: scripts/render_icon.sh            render both, and record what they were made from
#            scripts/render_icon.sh --check    check that nothing has changed since
#     (a folder may follow either, in place of macos/Resources)
#
# The pictures (icon.png, icon-small.png) are kept in git so that a build needs no SVG tools,
# which means a drawing can be changed and committed with its picture left as it was: every
# build would go on shipping the old one, and nothing would say so. So rendering writes down
# the checksum of each drawing and of each picture it made, in icon.rendered.sha256. The
# check compares those with the files as they are; scripts/build_app.sh runs it and stops
# if anything differs. Rendering needs rsvg-convert (brew install librsvg); checking does not.
set -euo pipefail

check=0
if [ "${1:-}" = --check ]; then check=1; shift; fi
dir=${1:-macos/Resources}
record=icon.rendered.sha256
fail() { echo "icon: $*" >&2; exit 1; }
cd "$dir" || fail "there is no folder $dir"

if [ "$check" = 1 ]; then
    [ -f "$record" ] || fail "$dir/$record is missing: render the pictures (scripts/render_icon.sh)"
    # shasum says one line a file, "<name>: OK" or "<name>: FAILED". Its verdicts are read from
    # those lines, not from how it exits: in a pipeline that is lost among the others'.
    said=$(shasum -a 256 -c "$record" 2>/dev/null || true)
    changed=$(echo "$said" | grep -v ': OK$' | cut -d: -f1 | tr '\n' ' ' || true)
    [ -z "${changed// /}" ] || fail "changed since the pictures were rendered: $changed
      Render them again and commit them with the change: scripts/render_icon.sh (it needs rsvg-convert)"
    [ "$(echo "$said" | grep -c ': OK$' || true)" -eq 4 ] \
        || fail "$dir/$record does not list the four files it should: render the pictures again (scripts/render_icon.sh)"
    echo "icon: the pictures in $dir are the drawings as they stand"
    exit 0
fi

command -v rsvg-convert >/dev/null || fail "rsvg-convert is not installed (brew install librsvg)"
rsvg-convert -w 1024 icon.svg -o icon.png
rsvg-convert -w 1024 icon-small.svg -o icon-small.png
shasum -a 256 icon.svg icon-small.svg icon.png icon-small.png > "$record"
echo "icon: rendered $dir/icon.png and $dir/icon-small.png, and recorded what they were made from"
