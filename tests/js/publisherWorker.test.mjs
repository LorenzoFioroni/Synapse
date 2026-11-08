import assert from 'node:assert/strict';
import { test } from 'node:test';

let listener;
let response = () => new Response(JSON.stringify({ app: 'synapse', capabilities: { publisher_pdfs: true } }));
const opened = [];
const event = { addListener() {} };
globalThis.chrome = {
  storage: {
    sync: { get: async () => ({ server: 'https://library.example' }) },
    local: { setAccessLevel: async () => {}, get: async () => ({}) },
    onChanged: event,
  },
  scripting: { unregisterContentScripts: async () => {}, registerContentScripts: async () => {} },
  permissions: { contains: async () => true, onAdded: event, onRemoved: event },
  runtime: {
    onInstalled: event, onStartup: event,
    onMessage: { addListener(fn) { listener = fn; } },
    getURL: (path) => `chrome-extension://test/${path}`,
  },
  windows: { create: async (options) => { opened.push(options); } },
};
globalThis.fetch = async () => response();
await import('../../browser_extension/background.js');
const launch = () => new Promise(resolve => listener({ type: 'find-publisher-pdfs', ids: ['chosen-item'] },
  { url: 'https://library.example/', frameId: 0 }, resolve));

test('the worker takes over only after this library supports authenticated scoped work', async () => {
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
  assert.equal(url.searchParams.has('token'), false);
});
