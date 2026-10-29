// Track only closers inserted by this editor, so typing into existing Markdown stays literal.
import type { EditorState } from '@codemirror/state';
import type { CM } from './editor.js';
import { scan, splitFrontmatter } from './mdscan.js';

interface Pair { id: number; open: number; close: number; char: string; size: number }

function literalAt(state: EditorState, from: number, to: number): boolean {
  const text = state.doc.toString();
  if (from < splitFrontmatter(text).bodyStart) return true;
  if ((/\\+$/.exec(text.slice(0, from))?.[0].length || 0) % 2) return true;
  return scan(text).spans.some((s) => s.kind !== 'gallery' && from >= s.from && to <= s.to
    && (from < s.to || s.open) && (from > s.from || !s.open));
}

export function delimiterPairing(cm: CM) {
  let nextId = 0;
  const editPairs = cm.StateEffect.define<{ drop?: number; add?: Pair[] }>({ map(value, mapping) {
    return { ...value, add: value.add?.map((p) => ({ ...p, open: mapping.mapPos(p.open, 1), close: mapping.mapPos(p.close, 1) })) };
  } });
  const restorePairs = cm.StateEffect.define<Pair[]>({ map(value, mapping) {
    return value.map((p) => ({ ...p, open: mapping.mapPos(p.open, 1), close: mapping.mapPos(p.close, 1) }));
  } });
  const pairs = cm.StateField.define<Pair[]>({
    create: () => [],
    update(value, tr) {
      const restored = tr.effects.filter((e) => e.is(restorePairs)).at(-1);
      if (restored) return restored.value.filter((p) => p.open >= 0 && p.close + p.size <= tr.newDoc.length
        && tr.newDoc.sliceString(p.open, p.open + p.size) === p.char.repeat(p.size)
        && tr.newDoc.sliceString(p.close, p.close + p.size) === p.char.repeat(p.size));
      const edits = tr.effects.filter((e) => e.is(editPairs)).map((e) => e.value);
      const dropped = new Set(edits.map((e) => e.drop));
      const next = value.filter((p) => !dropped.has(p.id)).map((p) => ({ ...p,
        open: tr.changes.mapPos(p.open, 1), close: tr.changes.mapPos(p.close, 1),
      })).filter((p) => p.close >= p.open + p.size
        && tr.newDoc.sliceString(p.open, p.open + p.size) === p.char.repeat(p.size)
        && tr.newDoc.sliceString(p.close, p.close + p.size) === p.char.repeat(p.size));
      return [...next, ...edits.flatMap((e) => e.add || [])];
    },
  });
  const history = cm.invertedEffects.of((tr) => tr.docChanged ? [restorePairs.of(tr.startState.field(pairs))] : []);
  const input = cm.EditorView.inputHandler.of((view, from, to, text) => {
    if ((text !== '$' && text !== '`') || view.state.readOnly || view.compositionStarted) return false;
    const state = view.state, main = state.selection.main;
    if (from !== main.from || to !== main.to) return false;
    const tracked = state.field(pairs);
    let handled = true;
    const changes = state.changeByRange((range) => {
      const pos = range.from;
      const emptyPair = tracked.find((p) => p.char === text && p.close === pos && p.open + p.size === pos);
      if (range.empty && emptyPair && emptyPair.size < (text === '$' ? 2 : 3)) {
        const size = emptyPair.size + 1;
        return { changes: { from: pos, insert: text + text }, range: cm.EditorSelection.cursor(pos + 1),
          effects: editPairs.of({ drop: emptyPair.id, add: [{ ...emptyPair, close: pos + 1, size }] }) };
      }
      if (range.empty && tracked.some((p) => p.char === text && pos >= p.close && pos < p.close + p.size)) {
        return { range: cm.EditorSelection.cursor(pos + 1) };
      }
      if (literalAt(state, range.from, range.to)) { handled = false; return { range }; }
      if (range.empty && (/[\p{L}\p{N}_]/u.test(state.sliceDoc(pos - 1, pos)) || /[\p{L}\p{N}_]/u.test(state.sliceDoc(pos, pos + 1)))) {
        handled = false; return { range };
      }
      const selected = state.sliceDoc(range.from, range.to);
      let size = 1, pad = '';
      if (text === '`') {
        size = Math.max(0, ...(selected.match(/`+/g) || []).map((r) => r.length)) + 1;
        if (/^`|`$/.test(selected)) pad = ' ';
      }
      const multi = selected.includes('\n');
      if (multi) size = Math.max(size, text === '$' ? 2 : 3);
      const mark = text.repeat(size), open = mark + (multi ? '\n' : pad), close = (multi ? '\n' : pad) + mark;
      const closing = range.to + open.length + (multi ? 1 : pad.length);
      return { changes: [{ from: range.from, insert: open }, { from: range.to, insert: close }],
        range: cm.EditorSelection.range(range.anchor + open.length, range.head + open.length),
        effects: editPairs.of({ add: [{ id: ++nextId, open: range.from, close: closing, char: text, size }] }) };
    });
    if (!handled) return false;
    view.dispatch(state.update(changes, { userEvent: 'input.type', scrollIntoView: true }));
    return true;
  });
  const keys = cm.keymap.of([
    { key: 'Backspace', run(view) {
      const state = view.state;
      if (state.readOnly) return false;
      const tracked = state.field(pairs);
      if (!state.selection.ranges.every((r) => r.empty && tracked.some((p) => p.open + p.size === r.head && p.close === r.head))) return false;
      view.dispatch(state.update(state.changeByRange((range) => {
        const p = tracked.find((p) => p.open + p.size === range.head && p.close === range.head)!;
        return { changes: { from: p.open, to: p.close + p.size }, range: cm.EditorSelection.cursor(p.open) };
      }), { userEvent: 'delete.backward', scrollIntoView: true }));
      return true;
    } },
    { key: 'Enter', run(view) {
      const state = view.state, range = state.selection.main;
      if (state.readOnly || !range.empty || state.selection.ranges.length !== 1) return false;
      const p = state.field(pairs).find((p) => p.char === '`' && p.size >= 3 && p.close === range.head
        && state.doc.lineAt(p.open).number === state.doc.lineAt(p.close).number);
      if (!p) return false;
      view.dispatch({ changes: { from: p.close, insert: '\n\n' }, selection: { anchor: p.close + 1 }, userEvent: 'input.type', scrollIntoView: true });
      return true;
    } },
  ]);
  return [pairs, input, keys, history];
}
