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
    } }).range(state.doc.lineAt(node.from).from, node.to));
  } });
  return cm.BlockWrapper.set(ranges, true);
}

/** The renderer sees just the table; its surrounding quotes are editor block wrappers. */
export function tableSource(doc: Text, node: SyntaxNode): string {
  let depth = 0;
  for (let p = node.parent; p; p = p.parent) if (p.name === 'Blockquote') depth++;
  return doc.sliceString(doc.lineAt(node.from).from, doc.lineAt(node.to).to).split('\n').map((line) => {
    for (let n = 0; n < depth; n++) line = line.replace(/^\s*>[ \t]?/, '');
    return line;
  }).join('\n');
}
