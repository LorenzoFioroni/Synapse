import { test } from 'node:test';
import assert from 'node:assert/strict';
import { changedSpan, lineDiff, markInlineChanges } from '../../frontend/js/views/notes/diff.js';

const apply = (a, c) => (c ? a.slice(0, c.from) + c.insert + a.slice(c.to) : a);

test('changedSpan replaces only what differs', () => {
  assert.equal(changedSpan('same', 'same'), null);
  assert.deepEqual(changedSpan('# T\n\nA [@x].\n', '# T\n\nA [@x; @y].\n'), { from: 10, to: 10, insert: '; @y' });
  assert.deepEqual(changedSpan('abc', ''), { from: 0, to: 3, insert: '' });
  assert.deepEqual(changedSpan('', 'abc'), { from: 0, to: 0, insert: 'abc' });
  assert.deepEqual(changedSpan('aaa', 'aaaa'), { from: 3, to: 3, insert: 'a' });
});

test('changedSpan never splits a surrogate pair and always rebuilds the text', () => {
  const pairs = [['x😀y', 'x😃y'], ['😀', '😃'], ['a😀', 'a'], ['😀b', '😀😀b'], ['ab', '😀ab']];
  for (const [a, b] of pairs) {
    const c = changedSpan(a, b);
    assert.equal(apply(a, c), b);
    assert.ok(!/^[\udc00-\udfff]/.test(c.insert) && !/[\ud800-\udbff]$/.test(c.insert), JSON.stringify(c));
  }
  const rnd = (n) => Array.from({ length: n }, () => 'ab\n😀 '[Math.floor(Math.random() * 5)]).join('');
  for (let k = 0; k < 2000; k++) {
    const a = rnd(Math.floor(Math.random() * 12)), b = rnd(Math.floor(Math.random() * 12));
    assert.equal(apply(a, changedSpan(a, b)), b);
  }
});

test('lineDiff still marks added and removed lines', () => {
  assert.deepEqual(lineDiff('a\nb', 'a\nc').map((r) => r.op + r.text), [' a', '-b', '+c']);
});

test('adjacent changed lines mark their changed words and escape text', () => {
  const rows = markInlineChanges(lineDiff('<same>\nThe old title', '<same>\nThe new title'));
  assert.equal(rows[0].html, '&lt;same&gt;');
  assert.equal(rows[1].html, 'The <del>old</del> title');
  assert.equal(rows[2].html, 'The <ins>new</ins> title');
});
