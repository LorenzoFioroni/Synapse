# Vendored libraries

Everything the frontend needs is served from this folder; nothing is loaded from a CDN.

| Folder | Library | Version | Licence | How to update |
|---|---|---|---|---|
| `pdfjs/` | pdf.js (Mozilla), legacy build (older Safari/iPadOS too) | 6.4.299 | Apache-2.0 (decoders in `pdfjs/wasm/`: their `LICENSE_*` files) | copy from the `pdfjs-dist` npm package (see `pdfjs/README`) |
| `cm6/` | CodeMirror 6 (notes editor), bundled with esbuild | see `tools/cm6/package-lock.json` (view 6.43.13, state 6.7.6, lang-markdown 6.5.2, …) | MIT (all bundled packages: `cm6/LICENSES.txt`) | `cd tools/cm6 && npm ci && ./build.sh` |
| `md/` | markdown-it 14.3.3 + markdown-it-footnote 4.0.0 + KaTeX 0.18.9 (note rendering), bundled with esbuild; `md/katex/` = KaTeX CSS + woff2 fonts | `tools/md/package-lock.json` | MIT, BSD-2 (entities) — `md/LICENSES.txt` | `cd tools/md && npm ci && ./build.sh` |
| `mermaid/` | Mermaid 12.2.0 (`mermaid.min.js`, loaded only when a note contains a diagram) | `tools/md/package-lock.json` | MIT | same as `md/` |
| `force-graph/` | force-graph 1.51.4 (`force-graph.min.js`, the graph in Notes; bundles d3-force), loaded when a graph is shown | `tools/graph/package-lock.json` | MIT (d3 modules: ISC) | `cd tools/graph && npm ci && ./build.sh` |

`cm6/cm6.js` is the entry point. The files in `cm6/chunks/` are loaded on demand (for example the
syntax highlighter for a code block's language), so the folder must be copied as a whole.

`cm6/cm6.d.ts` and `md/md.d.ts` give the bundles their TypeScript types: they repeat the re-exports of
`tools/cm6/entry.js` and `tools/md/entry.js` (the build scripts write them), and the types come from
the packages that `frontend/package.json` installs for type checking (`@codemirror/*`, `katex`,
`@types/markdown-it`). `force-graph` is typed the same way. When a library is updated, update both
`package.json` files to the same version.
