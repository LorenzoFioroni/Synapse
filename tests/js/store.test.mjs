import assert from 'node:assert/strict';
import { test } from 'node:test';
import { sortedItems, state } from '../../frontend/js/store.js';
import { hitBadge } from '../../frontend/js/util.js';

test('an unknown sort column sorts by title, in the chosen direction', () => {
  state.view = 'library';
  state.items = [{ id: '1', title: 'Beta' }, { id: '2', title: 'Alpha' }, { id: '3', title: 'Gamma' }];
  state.sort = { col: 'no-such-column', dir: 'desc' };
  assert.deepEqual(sortedItems().map((it) => it.title), ['Gamma', 'Beta', 'Alpha']);
  state.sort = { col: 'no-such-column', dir: 'asc' };
  assert.deepEqual(sortedItems().map((it) => it.title), ['Alpha', 'Beta', 'Gamma']);
});

test('a search match is badged by where its snippet comes from', () => {
  assert.equal(hitBadge(undefined), '');
  assert.match(hitBadge('…the <mark>Lindblad</mark> equation…'), />full text</);
  assert.match(hitBadge('<span class="hit-src">Note · Reading list</span> …<mark>Lindblad</mark>…'), />note</);
});
