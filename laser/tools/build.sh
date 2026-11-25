#!/bin/sh
# Assemble the static site into dist/
set -e
cd "$(dirname "$0")/.."
rm -rf dist && mkdir -p dist
cp -r web/* dist/
cp LICENSE dist/LICENSE.txt
cp -r src/lib src/formats src/templates dist/
cp node_modules/jszip/dist/jszip.min.js dist/vendor/
for w in 400 500 600 700; do cp node_modules/@fontsource/manrope/files/manrope-latin-$w-normal.woff2 dist/fonts/; done
for w in 500 600; do cp node_modules/@fontsource/sora/files/sora-latin-$w-normal.woff2 dist/fonts/; done
cp node_modules/@fontsource/manrope/LICENSE dist/fonts/LICENSE-Manrope.txt
cp node_modules/@fontsource/sora/LICENSE dist/fonts/LICENSE-Sora.txt
echo "built dist/ ($(du -sh dist | cut -f1))"
