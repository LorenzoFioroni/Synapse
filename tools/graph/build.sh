#!/bin/sh
# Rebuilds frontend/vendor/force-graph/ from the pinned version in package-lock.json.
# Usage: cd tools/graph && npm ci && ./build.sh
set -e
V=../../frontend/vendor/force-graph
rm -rf "$V" && mkdir -p "$V"
cp node_modules/force-graph/dist/force-graph.min.js "$V/"
cp node_modules/force-graph/LICENSE "$V/LICENSE"
echo "force-graph@$(node -p 'require("./node_modules/force-graph/package.json").version')  MIT (bundles d3-force and other d3 modules, ISC)" > "$V/VERSION"
echo "built: $(du -sh "$V" | cut -f1) force-graph"
