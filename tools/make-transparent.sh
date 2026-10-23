#!/usr/bin/env bash
# Make a GIF's flat background transparent and crop it to the area the animation uses.
# The background is flood-filled from the four corners, so same-coloured areas inside the
# subject (a white chest, a white paw) stay opaque.
#
# Usage: tools/make-transparent.sh in.gif out.gif [fuzz%]
set -euo pipefail
in=$1 out=$2 fuzz=${3:-12}
read -r w h < <(magick "$in[0]" -format '%w %h\n' info:)
x=$((w - 1)) y=$((h - 1))
tmp=$(mktemp --suffix=.gif)
trap 'rm -f "$tmp"' EXIT

magick "$in" -coalesce -alpha set -fuzz "$fuzz%" -fill none \
  -draw "color 0,0 floodfill" -draw "color $x,0 floodfill" \
  -draw "color 0,$y floodfill" -draw "color $x,$y floodfill" \
  -set dispose Background -loop 0 "$tmp"

# Union of the visible pixels over all frames, so the subject never gets cut off mid-animation.
box=$(magick "$tmp" -coalesce -alpha extract -evaluate-sequence max -trim -format '%wx%h%X%Y' info:)
magick "$tmp" -coalesce -crop "$box" +repage -set dispose Background -loop 0 "$out"
echo "$out: cropped to $box"
