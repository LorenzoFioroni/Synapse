import assert from 'node:assert/strict';
import { test } from 'node:test';
import { completionContext, noteChoices, searchItems, searchNotes, shortAuthors } from '../../frontend/js/views/notes/library.js';

const items = [
  { key: 'cuiVariational2015', authors: 'Cui, Jian; Cirac, J. Ignacio', title: 'Variational matrix product operators', year: 2015 },
  { key: 'smith2021', authors: 'Smith, Anna', title: 'Quantum things', year: 2021 },
  { key: 'zhangSmith2019', authors: 'Zhang, Wei; Smith, Bob', title: 'Other', year: 2019 },
  { key: 'gone', authors: 'Smith, X', title: 'Trashed', year: 2000, trashed: true },
  { key: 'bañuls2020', authors: 'Bañuls, Mari Carmen', title: 'Tensor networks', year: 2020 },
];

test('searchItems ranks key prefix, then author, then the rest; never trashed', () => {
  assert.deepEqual(searchItems(items, 'smith').map((i) => i.key), ['smith2021', 'zhangSmith2019']);
  assert.deepEqual(searchItems(items, 'cui var').map((i) => i.key), ['cuiVariational2015']);
  assert.deepEqual(searchItems(items, 'quantum').map((i) => i.key), ['smith2021']);
  assert.deepEqual(searchItems(items, 'banuls').map((i) => i.key), ['bañuls2020']);     // accents folded
  assert.equal(searchItems(items, '').length, 4);
  assert.deepEqual(searchItems(items, '').map((i) => i.year), [2021, 2020, 2019, 2015]);
});

test('note choices use folder/name only when names clash', () => {
  const c = noteChoices([{ path: 'Review.md' }, { path: 'a/Review.md' }, { path: 'papers/smith2021.md', title: 'Smith notes' }]);
  assert.deepEqual(c.map((x) => x.insert), ['Review', 'a/Review', 'smith2021']);
  assert.deepEqual(searchNotes(c, 'smith').map((x) => x.insert), ['smith2021']);
  assert.deepEqual(searchNotes(c, 'rev').map((x) => x.insert), ['Review', 'a/Review']);
});

test('completionContext', () => {
  assert.deepEqual(completionContext('see [@smi'), { kind: 'cite', query: 'smi', from: 6, bracket: true });
  assert.deepEqual(completionContext('see [a; @b'), { kind: 'cite', query: 'b', from: 9, bracket: true });
  assert.deepEqual(completionContext('as shown @cu'), { kind: 'cite', query: 'cu', from: 10, bracket: false });
  assert.deepEqual(completionContext('[-@x'), { kind: 'cite', query: 'x', from: 3, bracket: true });
  assert.equal(completionContext('mail me@exa'), null);
  assert.equal(completionContext('nothing here'), null);
  assert.deepEqual(completionContext('link [[Rev'), { kind: 'note', query: 'Rev', from: 7 });
  assert.deepEqual(completionContext('![[Rev'), { kind: 'note', query: 'Rev', from: 3 });
  assert.equal(completionContext('[[Done]] then'), null);
  assert.equal(completionContext('[x](y) @a').bracket, false);
});

test('shortAuthors', () => {
  assert.equal(shortAuthors('Cui, Jian; Cirac, J'), 'Cui & Cirac');
  assert.equal(shortAuthors('A, a; B, b; C, c'), 'A et al.');
});
