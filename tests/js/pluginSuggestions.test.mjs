import { test } from 'node:test';
import assert from 'node:assert/strict';
import { registerSuggestions } from '../../frontend/js/plugins/suggestions.js';
import { ghostSource } from '../../frontend/js/views/notes/hooks.js';

test('public suggestions replace, retire, and restore providers with change notifications', async () => {
  const changes = [];
  const off = ghostSource.onChange(() => changes.push(ghostSource.ready()));
  const a = registerSuggestions({ ready: () => true, complete: async () => 'first', delay: 100 });
  const controller = new AbortController();
  const b = registerSuggestions({ ready: () => false, complete: async (prefix, suffix, signal) => {
    assert.equal(signal, controller.signal);
    return prefix + suffix;
  } });
  assert.equal(ghostSource.ready(), false);
  assert.equal(await ghostSource.complete('pre', 'post', controller.signal), 'prepost');
  b();
  assert.equal(ghostSource.delay, 100);
  assert.equal(await ghostSource.complete('', '', controller.signal), 'first');
  a();
  assert.equal(ghostSource.ready(), false);
  assert.equal(await ghostSource.complete('', '', controller.signal), '');
  a(); b();
  assert.deepEqual(changes, [true, false, true, false]);
  off();
});
