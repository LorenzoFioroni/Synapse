import assert from 'node:assert/strict';
import { test } from 'node:test';
import { libraryPdfRequest } from '../../browser_extension/bridge.js';

const message = { type: 'find-publisher-pdfs', ids: ['item_1', 'item-2', 'item_1'] };
const sender = { url: 'https://library.example/', frameId: 0 };

test('only the configured library top frame can select existing-library IDs', () => {
  assert.deepEqual(libraryPdfRequest(message, sender, 'https://library.example'), ['item_1', 'item-2']);
  for (const url of ['https://other.example/', 'http://library.example/', 'https://library.example:8443/',
    'https://library.example.attacker.example/', 'data:text/html,x', 'bad URL']) {
    assert.equal(libraryPdfRequest(message, { ...sender, url }, 'https://library.example'), null);
  }
  assert.equal(libraryPdfRequest(message, { ...sender, frameId: 1 }, 'https://library.example'), null);
  assert.equal(libraryPdfRequest(message, {}, 'https://library.example'), null);
});

test('page messages cannot inject URLs, credentials, unbounded lists or malformed IDs', () => {
  for (const ids of [[], ['https://publisher.example/file.pdf'], ['../item'], ['a,b'], [''], [42], ['x'.repeat(129)], Array(201).fill('x')]) {
    assert.equal(libraryPdfRequest({ ...message, ids }, sender, 'https://library.example'), null);
  }
  for (const value of [null, {}, { ...message, type: 'fetch' }, 'find-publisher-pdfs']) {
    assert.equal(libraryPdfRequest(value, sender, 'https://library.example'), null);
  }
});
