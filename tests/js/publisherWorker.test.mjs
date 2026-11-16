import assert from 'node:assert/strict';
import { test } from 'node:test';

let listener;
// What the companion's native host answers a connector request: a Response, or a thrown error.
let response = () => new Response(JSON.stringify({ app: 'synapse', capabilities: { publisher_pdfs: true } }));
const opened = [];
const event = { addListener() {} };

/** A fake native messaging port: hello gives the library, requests go to `response`. */
function connectNative() {
  const listeners = [];
  return {
    onMessage: { addListener(fn) { listeners.push(fn); } },
    onDisconnect: { addListener() {} },
    postMessage(message) {
      queueMicrotask(async () => {
        let reply;
        if (message.type === 'hello') reply = { type: 'hello', server: 'https://library.example', name: 'PC' };
        else {
          try {
            const res = response();
            reply = { type: 'response', status: res.status, body: await res.text(), more: false };
          } catch (error) {
            reply = { type: 'error', message: error.message };
          }
        }
        for (const fn of listeners) fn({ id: message.id, ...reply });
      });
    },
  };
}

globalThis.chrome = {
  storage: {
    sync: { get: async () => ({}), remove: async () => {} },
    local: { setAccessLevel: async () => {}, get: async () => ({}), remove: async () => {} },
    onChanged: event,
  },
  scripting: { unregisterContentScripts: async () => {}, registerContentScripts: async () => {} },
  permissions: { contains: async () => true, onAdded: event, onRemoved: event },
  runtime: {
    onInstalled: event, onStartup: event,
    onMessage: { addListener(fn) { listener = fn; } },
    getURL: (path) => `chrome-extension://test/${path}`,
    getManifest: () => ({ version: '2.0.0' }),
    connectNative,
  },
  windows: { create: async (options) => { opened.push(options); } },
};
// Only build.json is fetched (absent here); the library is reached through the companion alone.
globalThis.fetch = async () => new Response('', { status: 404 });
await import('../../browser_extension/background.js');
const launch = () => new Promise(resolve => listener({ type: 'find-publisher-pdfs', ids: ['chosen-item'] },
  { url: 'https://library.example/', frameId: 0 }, resolve));

test('the worker takes over only after the library, reached through the companion, supports scoped work', async () => {
  for (const makeResponse of [
    () => new Response('{}', { status: 401 }),
    () => new Response(JSON.stringify({ app: 'synapse' })),
    () => { throw new Error('offline'); },
  ]) {
    response = makeResponse;
    assert.deepEqual(await launch(), { accepted: false });
    assert.equal(opened.length, 0);
  }
  response = () => new Response(JSON.stringify({ app: 'synapse', capabilities: { publisher_pdfs: true } }));
  assert.deepEqual(await launch(), { accepted: true });
  assert.equal(opened.length, 1);
  const url = new URL(opened[0].url);
  assert.equal(url.searchParams.get('ids'), 'chosen-item');
  assert.equal(url.searchParams.get('fallback'), '1');
  assert.equal(url.searchParams.get('library'), 'https://library.example');
  assert.equal(url.searchParams.has('token'), false);
});
