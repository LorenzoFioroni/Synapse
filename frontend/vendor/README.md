# Vendored libraries

Everything the frontend needs is served from this folder; nothing is loaded from a CDN.

| Folder | Library | Version | Licence | How to update |
|---|---|---|---|---|
| `pdfjs/` | pdf.js (Mozilla) | 3.4.120 | Apache-2.0 | copy from the `pdfjs-dist` npm package (see `pdfjs/README`) |
| `cm6/` | CodeMirror 6 (notes editor), bundled with esbuild | see `tools/cm6/package-lock.json` (view 6.43.13, state 6.7.6, lang-markdown 6.5.2, …) | MIT (all bundled packages: `cm6/LICENSES.txt`) | `cd tools/cm6 && npm ci && ./build.sh` |
| `md/` | markdown-it 14.3.2 + markdown-it-footnote 4.0.0 + KaTeX 0.18.9 (note rendering), bundled with esbuild; `md/katex/` = KaTeX CSS + woff2 fonts | `tools/md/package-lock.json` | MIT, BSD-2 (entities) — `md/LICENSES.txt` | `cd tools/md && npm ci && ./build.sh` |
| `mermaid/` | Mermaid 11.17.2 (`mermaid.min.js`, loaded only when a note contains a diagram) | `tools/md/package-lock.json` | MIT | same as `md/` |

`cm6/cm6.js` is the entry point. The files in `cm6/chunks/` are loaded on demand (for example the
syntax highlighter for a code block's language), so the folder must be copied as a whole.
