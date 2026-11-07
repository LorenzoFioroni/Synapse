import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as cm from '../../frontend/vendor/cm6/cm6.js';
import * as lib from '../../frontend/vendor/md/md.js';
import { toggleFormat, insertLink } from '../../frontend/js/views/notes/formatting.js';
import { delimiterPairing } from '../../frontend/js/views/notes/pairing.js';
import { underlineLanguage } from '../../frontend/js/views/notes/underline.js';
import { createRenderer } from '../../frontend/js/views/notes/render.js';

function editor(doc = '', anchor = 0, head = anchor) {
  let state = cm.EditorState.create({ doc, selection: { anchor, head }, extensions: [
    cm.EditorState.allowMultipleSelections.of(true), cm.history(),
    cm.markdown({ base: cm.markdownLanguage, extensions: [underlineLanguage(cm)] }), delimiterPairing(cm),
  ] });
  const view = { get state() { return state; }, compositionStarted: false, dispatch(tr) {
    state = tr.state || state.update(tr).state;
  } };
  return {
    view, text: () => state.doc.toString(), selection: () => state.selection.main,
    select(anchor, head = anchor) { view.dispatch({ selection: { anchor, head } }); },
    type(text) {
      for (const char of text) {
        const r = state.selection.main;
        if (!state.facet(cm.EditorView.inputHandler).some((f) => f(view, r.from, r.to, char))) {
          view.dispatch(state.update(state.replaceSelection(char), { userEvent: 'input.type' }));
        }
      }
    },
    key(key) { return state.facet(cm.keymap).flat().some((k) => k.key === key && k.run(view)); },
    format(kind) { toggleFormat(cm, kind)(view); },
  };
}

test('formatting wraps, toggles and preserves forward and backward selections', () => {
  for (const [kind, mark] of [['bold', '**'], ['italic', '*'], ['underline', '++'], ['strike', '~~'], ['code', '`']]) {
    for (const [anchor, head] of [[2, 6], [6, 2]]) {
      const e = editor('a word b', anchor, head);
      e.format(kind);
      assert.equal(e.text(), `a ${mark}word${mark} b`);
      assert.equal(e.view.state.sliceDoc(e.selection().from, e.selection().to), 'word');
      assert.equal(e.selection().anchor > e.selection().head, anchor > head);
      e.format(kind);
      assert.equal(e.text(), 'a word b');
    }
  }
});

test('empty formatting pairs, whitespace, nested markup and underscore forms', () => {
  const e = editor('');
  e.format('bold'); assert.equal(e.text(), '****'); assert.equal(e.selection().head, 2);
  e.format('bold'); assert.equal(e.text(), '');
  const ws = editor('  word  ', 0, 8); ws.format('italic'); assert.equal(ws.text(), '  *word*  ');
  const nested = editor('**a *word* b**', 7); nested.format('italic'); assert.equal(nested.text(), '**a word b**');
  const underscores = editor('__word__', 4); underscores.format('bold'); assert.equal(underscores.text(), 'word');
});

test('formatting a quoted list across lines leaves structural prefixes intact', () => {
  const e = editor('> - first\n> - second', 0, 20);
  e.format('bold'); assert.equal(e.text(), '> - **first**\n> - **second**');
  e.format('bold'); assert.equal(e.text(), '> - first\n> - second');
});

test('formatting is isolated from surrounding typing in undo history', () => {
  const e = editor('word', 0, 4); e.format('bold');
  e.select(6); e.type('x');
  assert.equal(e.text(), '**wordx**');
  cm.undo(e.view); assert.equal(e.text(), '**word**');
  cm.undo(e.view); assert.equal(e.text(), 'word');
  cm.redo(e.view); assert.equal(e.text(), '**word**');
});

test('inline code uses a delimiter longer than those in its content', () => {
  const e = editor('a ` b', 0, 5); e.format('code'); assert.equal(e.text(), '``a ` b``');
  e.format('code'); assert.equal(e.text(), 'a ` b');
  const edge = editor('`a`', 0, 3); edge.format('code');
  // Selecting an existing code span toggles it off.
  assert.equal(edge.text(), 'a');
});

test('formatting leaves math, fenced code, inline code and front matter alone', () => {
  for (const [text, at] of [['$x + y$', 3], ['$$\nx + y\n$$', 5], ['```\ncode\n```', 6], ['`code`', 3], ['---\ntitle: A\n---\ntext', 8]]) {
    const e = editor(text, at); e.format('bold'); assert.equal(e.text(), text);
  }
});

test('link shortcut wraps labels, selects the URL and edits existing links', () => {
  const e = editor('word', 0, 4); insertLink(cm)(e.view);
  assert.equal(e.text(), '[word](https://)');
  assert.equal(e.view.state.sliceDoc(e.selection().from, e.selection().to), 'https://');
  e.type('https://example.org'); e.select(2); insertLink(cm)(e.view);
  assert.equal(e.view.state.sliceDoc(e.selection().from, e.selection().to), 'https://example.org');
});

test('link shortcut escapes backslashes in the label so they cannot escape the bracket', () => {
  const e = editor('a\\b]', 0, 4); insertLink(cm)(e.view);
  assert.equal(e.text(), '[a\\\\b\\]](https://)');
});

test('dollars and backticks pair, surround selections, skip their generated closer and delete empty pairs', () => {
  for (const ch of ['$', '`']) {
    const e = editor(); e.type(ch); assert.equal(e.text(), ch + ch); assert.equal(e.selection().head, 1);
    e.type('x'); e.type(ch); assert.equal(e.text(), ch + 'x' + ch); assert.equal(e.selection().head, 3);
    const wrap = editor('word', 4, 0); wrap.type(ch); assert.equal(wrap.text(), ch + 'word' + ch);
    assert.equal(wrap.selection().anchor, 5); assert.equal(wrap.selection().head, 1);
    const empty = editor(); empty.type(ch); assert.ok(empty.key('Backspace')); assert.equal(empty.text(), '');
  }
});

test('double dollars and triple backticks can be typed naturally', () => {
  const math = editor(); math.type('$$'); assert.equal(math.text(), '$$$$'); assert.equal(math.selection().head, 2);
  math.type('x$$'); assert.equal(math.text(), '$$x$$'); assert.equal(math.selection().head, 5);
  const code = editor(); code.type('```python'); code.key('Enter');
  assert.equal(code.text(), '```python\n\n```'); assert.equal(code.selection().head, 10);
  code.type('x'); assert.equal(code.text(), '```python\nx\n```');
});

test('escaped delimiters and delimiters inside math/code stay literal', () => {
  for (const [doc, at, ch, want] of [
    ['\\', 1, '$', '\\$'], ['\\', 1, '`', '\\`'], ['`code`', 3, '$', '`co$de`'],
    ['$x + y$', 5, '`', '$x + `y$'], ['```\ncode\n```', 6, '$', '```\nco$de\n```'],
    ['---\ntitle: A\n---\ntext', 8, '$', '---\ntitl$e: A\n---\ntext'],
  ]) { const e = editor(doc, at); e.type(ch); assert.equal(e.text(), want); }
});

test('pairs follow edits before them and support multiple selections', () => {
  const e = editor('aa bb', 0);
  e.view.dispatch({ selection: cm.EditorSelection.create([cm.EditorSelection.range(0, 2), cm.EditorSelection.range(3, 5)]) });
  e.type('$'); assert.equal(e.text(), '$aa$ $bb$');
  e.select(1, 3); e.type('x'); e.type('$'); assert.equal(e.text(), '$x$ $bb$');
  e.select(5, 7); e.type('y'); e.type('$'); assert.equal(e.text(), '$x$ $y$');
});

test('underline nests formatting, links and citations without enabling HTML or changing code/math', () => {
  const r = createRenderer(lib);
  const out = r.render('++under **bold** and *italic* and [link](https://example.org)++');
  assert.match(out, /<u>under <strong>bold<\/strong> and <em>italic<\/em> and <a href=/);
  for (const text of ['C++', '++ unclosed ++', '+++literal+++', '`++code++`', '$x++y$']) assert.doesNotMatch(r.render(text), /<u>/);
  assert.match(r.render('<u>literal</u>'), /&lt;u&gt;/);
  assert.match(r.render('\\++literal++'), /\+\+literal\+\+/);
});

test('paired closer tracking survives undo and redo', () => {
  for (const ch of ['$', '`']) {
    const e = editor(); e.type(ch);
    cm.undo(e.view); assert.equal(e.text(), '');
    cm.redo(e.view); assert.equal(e.text(), ch + ch);
    e.select(1);
    e.type('x'); e.type(ch);
    assert.equal(e.text(), ch + 'x' + ch);
    assert.equal(e.selection().head, 3);
  }
});

test('undo/redo of a whole typing group restores valid delimiter tracking', () => {
  for (const initial of ['$abc', '```python']) {
    const e = editor(); e.type(initial);
    const before = e.text();
    cm.undo(e.view); assert.equal(e.text(), '');
    cm.redo(e.view); assert.equal(e.text(), before);
    e.select(initial.length);
    const closer = initial[0] === '$' ? '$' : '```';
    e.type(closer); assert.equal(e.text(), before);
    cm.undo(e.view); e.type('plain');
    assert.equal(e.text(), 'plain');
  }
});

test('underline parser and renderer agree on boundaries and nested marks', () => {
  const r = createRenderer(lib);
  for (const text of ['++word++', '++a **bold**++', 'C++', 'x+++y+++', '++ spaced ++', '\\++literal++', '`++code++`', 'a ++word++ b']) {
    const e = editor(text);
    let count = 0;
    cm.syntaxTree(e.view.state).iterate({ enter(node) { if (node.name === 'Underline') count++; } });
    assert.equal(count, (r.render(text).match(/<u>/g) || []).length, text);
  }
});

test('read-only editors do not accept formatting or paired insertions', () => {
  const e = editor('word', 0, 4);
  e.view.dispatch({effects: cm.StateEffect.appendConfig.of(cm.EditorState.readOnly.of(true))});
  e.format('bold'); assert.equal(e.text(), 'word');
  insertLink(cm)(e.view); assert.equal(e.text(), 'word');
  assert.ok(e.view.state.facet(cm.EditorView.inputHandler).every(f => !f(e.view, 0, 4, '$')));
});
