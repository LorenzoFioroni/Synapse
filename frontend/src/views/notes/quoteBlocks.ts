// Quote containers must wrap block widgets too: line backgrounds alone leave gaps at tables.
import type { Text, Range } from '@codemirror/state';
import type { BlockWrapper } from '@codemirror/view';
import type { SyntaxNode } from '@lezer/common';
import type { EditorState } from '@codemirror/state';
import type { CM } from './editor.js';
import type { Span } from './mdscan.js';

const TYPES = new Set(['note', 'tip', 'warning', 'question', 'quote', 'info', 'important', 'caution', 'example', 'todo']);

export function calloutAt(doc: Text, from: number) {
  const line = doc.lineAt(from);
  const m = /^>\s*\[!([A-Za-z]+)\]([+-]?)[ \t]*(.*)$/.exec(doc.sliceString(from, line.to));
  if (!m) return null;
  const type = TYPES.has(m[1].toLowerCase()) ? m[1].toLowerCase() : 'note';
  const markFrom = from + doc.sliceString(from, line.to).indexOf('[!');
  return { type, title: m[3], fallback: m[1][0].toUpperCase() + m[1].slice(1).toLowerCase(),
    markFrom, markTo: markFrom + m[1].length + 3 + m[2].length };
}

export function quoteWrappers(cm: CM, state: EditorState, spans: Span[]) {
  const ranges: Range<BlockWrapper>[] = [];
  cm.syntaxTree(state).iterate({ enter(node) {
    if (node.name !== 'Blockquote') return node.name === 'Document' || node.name.endsWith('List') || node.name === 'ListItem' ? undefined : false;
    if (spans.some((s) => s.kind !== 'gallery' && node.from >= s.from && node.to <= s.to)) return false;
    const callout = calloutAt(state.doc, node.from);
    let depth = 0;
    for (let p = node.node.parent; p; p = p.parent) if (p.name === 'Blockquote') depth++;
    ranges.push(cm.BlockWrapper.create({ tagName: 'div', rank: Math.max(0, 100 - depth), attributes: {
      class: callout ? `cm-lp-callout-block cm-lp-callout-${callout.type}` : 'cm-lp-quote-block',
    } }).range(state.doc.lineAt(node.from).from, quoteEnd(state.doc, node.node)));
  } });
  return cm.BlockWrapper.set(ranges, true);
}

function quoteDepth(node: SyntaxNode): number {
  let depth = 0;
  for (let p = node.parent; p; p = p.parent) if (p.name === 'Blockquote') depth++;
  return depth;
}

/** A line without its quote's markers, or null when one of them is missing (a "lazy" line). */
function unquoted(line: string, depth: number): string | null {
  for (let n = 0; n < depth; n++) {
    const m = /^\s*>[ \t]?/.exec(line);
    if (!m) return null;
    line = line.slice(m[0].length);
  }
  return line;
}

/**
 * Where a table inside a quote ends, as the read view's renderer (markdown-it) sees it.
 * CodeMirror's parser lets a quoted table go on over lines that lack the quote's ">", but the
 * renderer does not: without ">" on the header and delimiter lines there is no table at all
 * (null), and a row without it ends the table and the quote. Live preview draws only what the
 * read view draws, so a missing ">" shows up while editing.
 */
export function quotedTableEnd(doc: Text, node: SyntaxNode): number | null {
  const depth = quoteDepth(node);
  if (!depth) return node.to;
  const first = doc.lineAt(node.from).number, last = doc.lineAt(node.to).number;
  for (let n = first; n <= last; n++) {
    if (unquoted(doc.line(n).text, depth) !== null) continue;
    return n <= first + 1 ? null : doc.line(n - 1).to;
  }
  return node.to;
}

/**
 * A row without ">" ends a quoted table and, in the renderer, the quotes it lacks markers for;
 * the quote's box ends there too.
 */
export function quoteEnd(doc: Text, quote: SyntaxNode): number {
  const depth = quoteDepth(quote) + 1;
  let end = quote.to;
  const tables: SyntaxNode[] = [];
  const walk = (n: SyntaxNode) => {
    for (let c = n.firstChild; c; c = c.nextSibling) {
      if (c.name === 'Table') tables.push(c);
      else if (c.name === 'Blockquote' || c.name.endsWith('List') || c.name === 'ListItem') walk(c);
    }
  };
  walk(quote);
  for (const table of tables) {
    const cut = quotedTableEnd(doc, table);
    if (cut === null) continue;
    const next = doc.lineAt(cut).number + 1;
    if (next > doc.lineAt(table.to).number) continue;
    if (unquoted(doc.line(next).text, depth) === null) end = Math.min(end, cut);
  }
  return end;
}

/** The renderer sees just the table; its surrounding quotes are editor block wrappers. */
export function tableSource(doc: Text, node: SyntaxNode, to = node.to): string {
  const depth = quoteDepth(node);
  return doc.sliceString(doc.lineAt(node.from).from, doc.lineAt(to).to).split('\n')
    .map((line) => unquoted(line, depth) ?? line).join('\n');
}
