// Types of md.js (tools/md/entry.js; written by tools/md/build.sh). The types come from katex and
// @types/markdown-it, installed in frontend/ for type checking at the same versions.
import type { PluginSimple } from 'markdown-it';

export { default as MarkdownIt } from 'markdown-it';
export { default as katex } from 'katex';
export declare const footnote: PluginSimple;
