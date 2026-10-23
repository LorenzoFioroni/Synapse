// History retains distinct library destinations and can repair removed plugin destinations.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { initNav, navCurrent, navPush, navReplace, navSnapshot, navUpdate } from '../../frontend/js/nav.js';

function setup(initial, navigate = () => {}) {
  const entries = [];
  const listeners = new Map();
  globalThis.history = {
    state: null,
    replaceState(state) { this.state = structuredClone(state); entries[entries.length - 1] = this.state; },
    pushState(state) { this.state = structuredClone(state); entries.push(this.state); },
  };
  globalThis.window = { addEventListener: (type, fn) => listeners.set(type, fn) };
  entries.push(null);
  navSnapshot(() => null);
  initNav(initial, navigate);
  return { entries, restore: async (entry) => {
    history.state = { zc: entry };
    await listeners.get('popstate')({ state: history.state });
  } };
}

test('different library views have their own history, selecting in the same view does not', () => {
  const { entries } = setup({ ws: 'library', view: 'library', sel: ['a'] });
  navPush({ ws: 'library', view: 'source:example.today', sel: [] });
  navPush({ ws: 'library', view: 'source:example.today', sel: ['x'] });
  navPush({ ws: 'library', view: 'folder:1', sel: [] });
  assert.deepEqual(entries.map((entry) => entry.zc), [
    { ws: 'library', view: 'library', sel: ['a'] },
    { ws: 'library', view: 'source:example.today', sel: ['x'] },
    { ws: 'library', view: 'folder:1', sel: [] },
  ]);
});

test('leaving the library snapshots its latest selection before recording another workspace', () => {
  const { entries } = setup({ ws: 'library', view: 'folder:1', sel: [] });
  navSnapshot((entry) => entry.ws === 'library' ? { sel: ['a', 'b'] } : null);
  navPush({ ws: 'tasks', task: 'one' });
  navUpdate('tasks', { task: 'two' });
  assert.deepEqual(entries.map((entry) => entry.zc), [
    { ws: 'library', view: 'folder:1', sel: ['a', 'b'] },
    { ws: 'tasks', task: 'two' },
  ]);
});

test('restoring an unavailable plugin replaces the destination without adding history', async () => {
  const fallback = { ws: 'library', view: 'library', sel: [] };
  const { entries, restore } = setup(fallback, async () => {
    await Promise.resolve();
    navPush({ ws: 'notes', path: null });
    navUpdate('page', { page: 'unexpected' });
    assert.equal(navCurrent().page, 'stopped.main');
    navReplace(fallback);
  });
  await restore({ ws: 'page', page: 'stopped.main' });
  assert.equal(entries.length, 1);
  assert.deepEqual(navCurrent(), fallback);
  navPush({ ws: 'tasks', task: null });
  assert.equal(entries.length, 2);
});
