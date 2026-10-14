import assert from 'node:assert/strict';
import { test } from 'node:test';
import { sortedItems, state } from '../../frontend/js/store.js';

test('an unknown sort column sorts by title, in the chosen direction', () => {
  state.view = 'library';
  state.items = [{ id: '1', title: 'Beta' }, { id: '2', title: 'Alpha' }, { id: '3', title: 'Gamma' }];
  state.sort = { col: 'no-such-column', dir: 'desc' };
  assert.deepEqual(sortedItems().map((it) => it.title), ['Gamma', 'Beta', 'Alpha']);
  state.sort = { col: 'no-such-column', dir: 'asc' };
  assert.deepEqual(sortedItems().map((it) => it.title), ['Alpha', 'Beta', 'Gamma']);
});
