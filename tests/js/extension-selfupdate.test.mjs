import assert from 'node:assert/strict';
import { test } from 'node:test';
import { recordBuild, reloadIfUpdated } from '../../browser_extension/selfUpdate.js';

/** A fake chrome with a storage.local and a build.json that answers ``file.build`` (null: no file). */
function setup(file, stored = {}) {
  const calls = { reload: 0 };
  globalThis.chrome = {
    runtime: { getURL: (p) => `chrome-extension://x/${p}`, reload: () => { calls.reload += 1; } },
    storage: { local: {
      get: async (keys) => Object.fromEntries(keys.filter((k) => k in stored).map((k) => [k, stored[k]])),
      set: async (o) => Object.assign(stored, o),
      remove: async (keys) => keys.forEach((k) => delete stored[k]),
    } },
  };
  globalThis.fetch = async () => (file.build === null ? { ok: false } : { ok: true, json: async () => ({ build: file.build }) });
  return { calls, stored };
}

test('a repository checkout (no build.json) never reloads', async () => {
  const { calls } = setup({ build: null }, { loadedBuild: 'a' });
  await reloadIfUpdated();
  assert.equal(calls.reload, 0);
});

test('the build seen at load is recorded; the same build does not reload', async () => {
  const { calls, stored } = setup({ build: 'a' });
  await reloadIfUpdated();   // nothing recorded yet: onInstalled has not run
  assert.equal(calls.reload, 0);
  await recordBuild();
  assert.equal(stored.loadedBuild, 'a');
  await reloadIfUpdated();
  assert.equal(calls.reload, 0);
});

test('a different build on disk reloads once, and never again for the same build', async () => {
  const { calls } = setup({ build: 'b' }, { loadedBuild: 'a' });
  await reloadIfUpdated();
  assert.equal(calls.reload, 1);
  await reloadIfUpdated();   // the reload did not record the new build: no loop
  assert.equal(calls.reload, 1);
});
