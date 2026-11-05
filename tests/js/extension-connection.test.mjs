import assert from 'node:assert/strict';
import { test } from 'node:test';
import { serverAddress, loadConnection, libraryFetch } from '../../browser_extension/connection.js';

test('local addresses preserve HTTP and remote addresses use HTTPS', () => {
  assert.equal(serverAddress('').server, 'http://127.0.0.1:8000');
  assert.equal(serverAddress('localhost:5050').server, 'http://localhost:5050');
  assert.equal(serverAddress('[::1]:8000').local, true);
  assert.equal(serverAddress('synapse.example').server, 'https://synapse.example');
  assert.equal(serverAddress('https://synapse.example/').local, false);
  assert.equal(serverAddress('https://synapse.example:8443').permission, 'https://synapse.example/*');
  for (const address of ['http://synapse.example', 'https://user:password@synapse.example',
    'https://synapse.example/path', 'https://synapse.example?secret=x', 'https://synapse.example/#x']) {
    assert.throws(() => serverAddress(address));
  }
});

test('tokens are loaded only for the server they were saved for', async () => {
  let server = 'https://synapse.example';
  globalThis.chrome = { storage: {
    sync: { get: async () => ({ server }) },
    local: { get: async () => ({ connection: { server: 'https://synapse.example', token: 'secret' } }) },
  } };
  assert.equal((await loadConnection()).token, 'secret');
  server = 'https://other.example';
  assert.equal((await loadConnection()).token, '');
});

test('library requests authenticate without cookies or following redirects', async () => {
  const previous = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => ({ url, options });
    const { url, options } = await libraryFetch('https://synapse.example', 'secret', '/api/connector/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    assert.equal(url, 'https://synapse.example/api/connector/save');
    assert.equal(options.headers.get('Authorization'), 'Bearer secret');
    assert.equal(options.headers.get('Content-Type'), 'application/json');
    assert.equal(options.credentials, 'omit');
    assert.equal(options.redirect, 'error');
    const local = await libraryFetch('http://localhost:8000', '', '/api/connector/ping');
    assert.equal(local.options.headers.has('Authorization'), false);
  } finally {
    globalThis.fetch = previous;
  }
});
