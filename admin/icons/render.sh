#!/bin/sh
# PNG-иконки для admin/static: rsvg-convert (librsvg2-bin) и optipng.
set -e
cd "$(dirname "$0")"
out=../static
rsvg-convert -w 192 -h 192 "$out/favicon.svg" -o "$out/icon-192.png"
rsvg-convert -w 512 -h 512 "$out/favicon.svg" -o "$out/icon-512.png"
rsvg-convert -w 512 -h 512 maskable.svg -o "$out/icon-maskable-512.png"
rsvg-convert -w 180 -h 180 apple-touch-icon.svg -o "$out/apple-touch-icon.png"
optipng -quiet -o7 -strip all "$out"/icon-*.png "$out/apple-touch-icon.png"
