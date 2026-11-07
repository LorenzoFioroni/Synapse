// Markdown editing commands. Each command is one undo step and keeps the selected text selected.
import type { EditorState, SelectionRange } from '@codemirror/state';
import type { EditorView } from '@codemirror/view';
import type { CM } from './editor.js';
import { scan, splitFrontmatter } from './mdscan.js';

export type TextFormat = 'bold' | 'italic' | 'underline' | 'strike' | 'code';
const FORMATS = {
  bold: { mark: '**', node: 'StrongEmphasis', token: 'EmphasisMark' },
  italic: { mark: '*', node: 'Emphasis', token: 'EmphasisMark' },
  underline: { mark: '++', node: 'Underline', token: 'UnderlineMark' },
  strike: { mark: '~~', node: 'Strikethrough', token: 'StrikethroughMark' },
  code: { mark: '`', node: 'InlineCode', token: 'CodeMark' },
};

function protectedRange(state: EditorState, range: SelectionRange, code = false): boolean {
  const text = state.doc.toString();
  if (range.from < splitFrontmatter(text).bodyStart) return true;
  return scan(text).spans.some((s) => range.from >= s.from && range.to <= s.to
    && (s.kind === 'math' || s.kind === 'mathblock' || s.kind === 'fence' || s.kind === 'taskblock' || s.kind === 'comment' || (!code && s.kind === 'code')));
}

export function toggleFormat(cm: CM, format: TextFormat) {
  return (view: EditorView): boolean => {
    const { state } = view;
    if (state.readOnly || state.selection.ranges.some((r) => protectedRange(state, r, format === 'code'))) return true;
    const def = FORMATS[format];
    const change = state.changeByRange((range) => {
      const remove = (a: number, b: number, c: number, d: number) => {
        const changes = [{ from: a, to: b }, { from: c, to: d }];
        const map = state.changes(changes);
        return { changes, range: cm.EditorSelection.range(map.mapPos(range.anchor), map.mapPos(range.head)) };
      };
      // The parser distinguishes italic inside bold (and vice versa), including underscore syntax.
      if (state.selection.ranges.length === 1) {
        for (let node = cm.syntaxTree(state).resolveInner(range.from, 1); node; node = node.parent!) {
          if (node.name !== def.node || node.to < range.to) continue;
          const marks = node.getChildren(def.token);
          if (marks.length >= 2) return remove(marks[0].from, marks[0].to, marks.at(-1)!.from, marks.at(-1)!.to);
        }
      }
      const selected = state.sliceDoc(range.from, range.to);
      let from = range.from, to = range.to;
      // Whitespace stays outside the markers, otherwise Markdown emphasis would not render.
      if (!range.empty) {
        from += /^\s*/.exec(selected)![0].length;
        to -= /\s*$/.exec(selected)![0].length;
        if (to <= from) return { range };
      }
      let mark = def.mark;
      if (format === 'code') {
        const runs = state.sliceDoc(from, to).match(/`+/g) || [];
        mark = '`'.repeat(Math.max(0, ...runs.map((r) => r.length)) + 1);
      }
      if (state.sliceDoc(from - mark.length, from) === mark && state.sliceDoc(to, to + mark.length) === mark) {
        return remove(from - mark.length, from, to, to + mark.length);
      }
      // Format each nonempty selected line, leaving quote/list prefixes and indentation intact.
      const changes: { from: number; to?: number; insert: string }[] = [];
      if (state.doc.lineAt(from).number !== state.doc.lineAt(to).number) {
        for (let pos = from; pos < to;) {
          const line = state.doc.lineAt(pos), end = Math.min(line.to, to);
          const raw = state.sliceDoc(pos, end);
          const lead = state.sliceDoc(line.from, pos).trim() ? /^\s*/ : /^(?:\s*>\s*)*(?:\s*(?:[-*+]|\d+[.)])\s+)?\s*/;
          const prefix = lead.exec(raw)![0].length;
          const start = pos + prefix, stop = end - /\s*$/.exec(raw)![0].length;
          if (stop > start) {
            if (state.sliceDoc(start, start + mark.length) === mark && state.sliceDoc(stop - mark.length, stop) === mark && stop - start >= 2 * mark.length) {
              changes.push({ from: start, to: start + mark.length, insert: '' }, { from: stop - mark.length, to: stop, insert: '' });
            } else changes.push({ from: start, insert: mark }, { from: stop, insert: mark });
          }
          pos = line.to + 1;
        }
        const map = state.changes(changes);
        return { changes, range: cm.EditorSelection.range(map.mapPos(range.anchor, -1), map.mapPos(range.head, 1)) };
      }
      const pad = format === 'code' && /^`|`$/.test(state.sliceDoc(from, to)) ? ' ' : '';
      const open = mark + pad, close = pad + mark;
      changes.push({ from, insert: open }, { from: to, insert: close });
      const forward = range.anchor <= range.head;
      return { changes, range: cm.EditorSelection.range(
        forward ? from + open.length : to + open.length,
        forward ? to + open.length : from + open.length,
      ) };
    });
    view.dispatch(state.update(change, { userEvent: 'input.format', scrollIntoView: true,
      annotations: cm.isolateHistory.of('full') }));
    return true;
  };
}

export function insertLink(cm: CM) {
  return (view: EditorView): boolean => {
    const { state } = view;
    if (state.readOnly || state.selection.ranges.some((r) => protectedRange(state, r))) return true;
    const changes = state.changeByRange((range) => {
      for (let node = cm.syntaxTree(state).resolveInner(range.from, 1); node; node = node.parent!) {
        if (node.name === 'Link' && node.to >= range.to) {
          const url = node.getChild('URL');
          if (url) return { range: cm.EditorSelection.range(url.from, url.to) };
        }
      }
      const text = state.sliceDoc(range.from, range.to).replace(/\n/g, ' ').replace(/[[\]\\]/g, '\\$&');
      const insert = `[${text}](https://)`;
      const from = range.from + (text ? text.length + 3 : 1);
      return { changes: { from: range.from, to: range.to, insert },
        range: cm.EditorSelection.range(from, text ? from + 8 : from) };
    });
    view.dispatch(state.update(changes, { userEvent: 'input.format', scrollIntoView: true,
      annotations: cm.isolateHistory.of('full') }));
    return true;
  };
}

export function formattingKeys(cm: CM) {
  return [
    { key: 'Mod-b', run: toggleFormat(cm, 'bold') },
    { key: 'Mod-i', run: toggleFormat(cm, 'italic') },
    { key: 'Mod-u', run: toggleFormat(cm, 'underline') },
    { key: 'Mod-Shift-x', run: toggleFormat(cm, 'strike') },
    { key: 'Mod-Shift-c', run: toggleFormat(cm, 'code') },
    { key: 'Mod-k', run: insertLink(cm) },
  ].map((key) => ({ ...key, preventDefault: true }));
}
