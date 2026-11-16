import assert from 'node:assert/strict';
import { test } from 'node:test';
import { companionHello, libraryFetch } from '../../browser_extension/connection.js';

/** A fake native host: `handler(message, reply, port)` answers each message the extension posts. */
let previous = [];
function fakeHost(handler) {
  previous.forEach((port) => port.close());   // the module keeps its port between tests
  const state = { connects: 0, sent: [], lastError: undefined };
  const ports = previous = [];
  globalThis.chrome = {
    runtime: {
      get lastError() { return state.lastError; },
      getURL: (path) => `chrome-extension://x/${path}`,
      getManifest: () => ({ version: '2.0.0' }),
      connectNative(name) {
        assert.equal(name, 'org.synapse.companion');
        state.connects += 1;
        const listeners = []; const closers = [];
        const port = {
          onMessage: { addListener: (fn) => listeners.push(fn) },
          onDisconnect: { addListener: (fn) => closers.push(fn) },
          postMessage(message) {
            state.sent.push(message);
            queueMicrotask(() => handler(message, (reply) => listeners.forEach((fn) => fn({ id: message.id, ...reply })), port));
          },
          close: () => closers.forEach((fn) => fn()),
        };
        ports.push(port);
        return port;
      },
    },
  };
  globalThis.fetch = async () => ({ ok: false });
  return state;
}

test('hello reports the build and returns the server and computer name', async () => {
  const state = fakeHost((m, reply) => reply({ type: 'hello', server: 'https://synapse.example', name: 'Laptop' }));
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ build: 'abc' }) });
  assert.deepEqual(await companionHello(), { server: 'https://synapse.example', name: 'Laptop' });
  assert.deepEqual({ ...state.sent[0], id: 0 }, { id: 0, type: 'hello', version: '2.0.0', build: 'abc' });
});

test('requests send JSON bodies and join chunked replies into a Response', async () => {
  const state = fakeHost((m, reply) => {
    reply({ type: 'response', status: 409, body: '{"er', more: true });
    reply({ type: 'response', status: 409, body: 'ror":"dup"}', more: false });
  });
  const res = await libraryFetch('/api/connector/save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"a":1}',
  });
  assert.equal(res.ok, false);
  assert.equal(res.status, 409);
  assert.deepEqual(await res.json(), { error: 'dup' });
  const sent = state.sent.at(-1);
  assert.equal(sent.type, 'request');
  assert.equal(sent.method, 'POST');
  assert.equal(sent.path, '/api/connector/save');
  assert.deepEqual(sent.json, { a: 1 });
  assert.equal(typeof sent.id, 'string');
});

test('form data entries become strings and base64 files', async () => {
  const state = fakeHost((m, reply) => reply({ type: 'response', status: 200, body: '{}', more: false }));
  const form = new FormData();
  form.append('meta', '{"x":1}');
  form.append('file', new Blob([new Uint8Array([1, 2, 3])], { type: 'application/pdf' }), 'p.pdf');
  await libraryFetch('/api/connector/save', { method: 'POST', body: form });
  assert.deepEqual(state.sent.at(-1).form, [
    ['meta', '{"x":1}'],
    ['file', { data: 'AQID', filename: 'p.pdf', type: 'application/pdf' }],
  ]);
});

test('a host error rejects with its message', async () => {
  fakeHost((m, reply) => reply({ type: 'error', message: 'Not connected to a library.' }));
  await assert.rejects(libraryFetch('/api/connector/ping'), /Not connected to a library/);
  await assert.rejects(companionHello(), /Not connected/);
});

test('an aborted request rejects with AbortError and ignores the late reply', async () => {
  let late;
  fakeHost((m, reply) => { late = reply; });
  const controller = new AbortController();
  const result = libraryFetch('/api/connector/ping', { signal: controller.signal });
  await new Promise((r) => setTimeout(r, 5));
  controller.abort();
  await assert.rejects(result, (e) => e.name === 'AbortError');
  late({ type: 'response', status: 200, body: '{}', more: false });   // must not throw
  await assert.rejects(libraryFetch('/api/connector/ping', { signal: controller.signal }), (e) => e.name === 'AbortError');
});

test('a closed port rejects pending requests and is reopened for the next one', async () => {
  let first = true;
  const state = fakeHost((m, reply, port) => {
    if (first) { first = false; state.lastError = { message: 'Native host has exited.' }; port.close(); return; }
    reply({ type: 'response', status: 200, body: '{"ok":true}', more: false });
  });
  await assert.rejects(libraryFetch('/api/connector/ping'), /Native host has exited/);
  state.lastError = undefined;
  assert.deepEqual(await (await libraryFetch('/api/connector/ping')).json(), { ok: true });
  assert.equal(state.connects, 2);
});
