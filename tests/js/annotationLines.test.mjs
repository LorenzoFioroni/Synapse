import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mergeLines, wordEnd, wordStart } from '../../frontend/js/viewer/annotations/lines.js';

const box = (left, top, right, bottom) => ({ left, top, right, bottom });

test('a line with inline math becomes one box at the height of its words', () => {
  const merged = mergeLines([
    box(10, 100, 120, 110),   // words
    box(122, 97, 140, 109),   // a formula, set a little higher
    box(141, 104, 146, 112),  // its subscript
    box(150, 100, 300, 110),  // words
  ]);
  assert.deepEqual(merged, [box(10, 100, 300, 110)]);
});

test('lines stay apart, and so do two columns at the same height', () => {
  const merged = mergeLines([
    box(10, 100, 200, 110), box(10, 112, 200, 122),       // two lines of the left column
    box(260, 100, 450, 110),                              // the right column, level with the first
  ]);
  assert.deepEqual(merged, [box(10, 100, 200, 110), box(10, 112, 200, 122), box(260, 100, 450, 110)]);
});

test('a line that starts with a subscript still joins the words after it', () => {
  const merged = mergeLines([box(10, 104, 14, 112), box(15, 100, 200, 110)]);
  assert.deepEqual(merged, [box(10, 100, 200, 110)]);
});

test('word bounds: a selection inside words grows to whole words', () => {
  const t = 'the quick brown fox';
  assert.equal(wordStart(t, 6), 4);          // q[u]ick → quick
  assert.equal(wordEnd(t, 12), 15);          // br[o]wn → brown
  assert.equal(wordStart(t, 4), 4);          // already at a word's start
  assert.equal(wordEnd(t, 9), 9);            // already at a word's end
});

test('word bounds: spaces at the ends are dropped, and a boundary next to a space stays', () => {
  const t = 'the quick brown fox';
  assert.equal(wordStart(t, 3), 4);          // starts on the space before "quick"
  assert.equal(wordEnd(t, 10), 9);           // ends after the space following "quick"
  assert.equal(wordEnd(t, 0), 0);            // ends where the text starts: nothing to grow
  assert.equal(wordStart(t, t.length), t.length);
  assert.equal(wordStart('a   ', 1, 3), 3);  // never past the end of the selection
});

test('word bounds: letters with accents and apostrophes are part of words', () => {
  assert.equal(wordStart("Bañuls's method", 3), 0);
  assert.equal(wordEnd("Bañuls's method", 3), 8);
  assert.equal(wordEnd('x-axis', 1), 1);     // a hyphen ends a word
});
