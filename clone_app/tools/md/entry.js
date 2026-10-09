// Rendering libraries for notes: markdown-it (+ footnotes) and KaTeX, re-exported from one module.
// Mermaid is not bundled: vendor/mermaid/mermaid.min.js is loaded only when a note has a diagram.
export { default as MarkdownIt } from 'markdown-it';
export { default as footnote } from 'markdown-it-footnote';
export { default as katex } from 'katex';
