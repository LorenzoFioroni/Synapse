#!/bin/sh
# Rebuilds frontend/vendor/cm6/ from the pinned versions in package-lock.json.
# Usage: cd tools/cm6 && npm ci && ./build.sh
set -e
OUT=../../frontend/vendor/cm6
rm -rf "$OUT" && mkdir -p "$OUT"
npx esbuild entry.js --bundle --format=esm --splitting --minify --target=es2020 \
  --outdir="$OUT" --entry-names=cm6 --chunk-names=chunks/[name]-[hash] --legal-comments=none
cp LICENSES.txt "$OUT/LICENSES.txt"
echo "built $(du -sh "$OUT" | cut -f1) into $OUT"
