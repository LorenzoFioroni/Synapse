#!/bin/sh
# Rebuilds frontend/vendor/cm6/ from the pinned versions in package-lock.json.
# Usage: cd tools/cm6 && npm ci && ./build.sh
set -e
OUT=../../frontend/vendor/cm6
rm -rf "$OUT" && mkdir -p "$OUT"
npx esbuild entry.js --bundle --format=esm --splitting --minify --target=es2020 \
  --outdir="$OUT" --entry-names=cm6 --chunk-names=chunks/[name]-[hash] --legal-comments=none
cp LICENSES.txt "$OUT/LICENSES.txt"
# types for TypeScript (frontend/src): the same re-exports; frontend/package.json pins the same versions
{ echo "// Types of cm6.js: the same re-exports as tools/cm6/entry.js (copied by tools/cm6/build.sh). The types"
  echo "// come from the @codemirror packages installed in frontend/ for type checking, at the same versions."
  tail -n +3 entry.js; } > "$OUT/cm6.d.ts"
echo "built $(du -sh "$OUT" | cut -f1) into $OUT"
