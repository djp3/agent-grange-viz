#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Write the agent-grange-viz app icon as an .icns.
#     usage: scripts/make_icon.sh <icon.png> <icon-small.png> <out.icns>
#
# The icon is drawn in macos/Resources/icon.svg: the grange hall, and around it the ring the
# granges gossip on, each grange a light (green, one yellow, one red), on the dark rounded
# square agent-farm's and agent-frolic's icons also sit on. macos/Resources/icon.png is that
# drawing at 1024 px, margin and rounded square included. At 16 and 32 px the whole scene
# runs together, so those two sizes are made from a second drawing, three granges and the
# lines between them (icon-small.svg, icon-small.png).
# This only scales the two pictures to the sizes an .icns holds, with the tools every Mac has
# (sips, iconutil), so a build needs no SVG tools. How a drawing is rendered to its picture
# is scripts/render_icon.sh's to say. Both drawings are this project's own work, under its
# own license: there is no artwork of anybody else's to credit.
set -euo pipefail

[ $# -eq 3 ] || { echo "usage: scripts/make_icon.sh <icon.png> <icon-small.png> <out.icns>" >&2; exit 2; }
src=$1
small=$2
out=$3
fail() { echo "make_icon: $*" >&2; exit 1; }

# Each thing that can be wrong with a picture is said as what it is: the names were given,
# so it is not a matter of how this is used.
check() {
    local picture=$1 wide high
    [ -f "$picture" ] || fail "there is no $picture (the icon is made from the drawings at 1024 px, in macos/Resources)"
    # A PNG by its first bytes, not by its name, which is all sips goes by. Given a drawing
    # itself (icon.svg), sips would scale what the system's SVG renderer makes of it: no
    # shadows, no glow, and no word that anything is wrong.
    [ "$(head -c 8 "$picture" | xxd -p)" = 89504e470d0a1a0a ] \
        || fail "$picture is not a PNG. Give it the drawing rendered at 1024 px (icon.png), not the drawing itself (icon.svg)"
    # A square of 1024 px or more, as sips measures it. Anything else would be scaled all the
    # same, drawn out of shape or blown up from too little, with nothing said.
    read -r wide high <<< "$(sips -g pixelWidth -g pixelHeight "$picture" 2>/dev/null | awk '/pixel(Width|Height):/ {print $2}' | tr '\n' ' ')"
    [[ "${wide:-}" =~ ^[0-9]+$ && "${high:-}" =~ ^[0-9]+$ ]] || fail "$picture cannot be read as a picture"
    [ "$wide" -eq "$high" ] || fail "$picture is $wide by $high px: the icon is square, and this would be drawn out of shape"
    [ "$wide" -ge 1024 ] \
        || fail "$picture is $wide px: the icon's largest size is 1024 px, and this would be blown up to it (render the drawings again: scripts/render_icon.sh)"
}
check "$src"
check "$small"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
set="$work/AppIcon.iconset"
# iconutil will not make the folder it writes into, and in a fresh clone there is none: the
# icon is all it would hold, and git ignores the icon.
mkdir -p "$set" "$(dirname "$out")"
# Seven sizes fill the ten names an .icns has: 32, 256 and 512 px each serve twice, as a size
# of their own and as the size below them doubled. Each is scaled once: 16 and 32 px from the
# three granges alone, the rest from the whole scene.
for px in 16 32; do
    sips -z "$px" "$px" "$small" --out "$work/$px.png" >/dev/null
done
for px in 64 128 256 512 1024; do
    sips -z "$px" "$px" "$src" --out "$work/$px.png" >/dev/null
done
for base in 16 32 128 256 512; do
    cp "$work/$base.png" "$set/icon_${base}x${base}.png"
    cp "$work/$((base * 2)).png" "$set/icon_${base}x${base}@2x.png"
done
iconutil -c icns "$set" -o "$out"
echo "wrote $out"
