import assert from 'node:assert/strict';
import { test } from 'node:test';
import { requestPublisherPdfs } from '../../frontend/js/publisherPdfs.js';

test('without the extension, PDF follow-ups fall back to server sources', async () => {
  globalThis.document = new EventTarget();
  document.documentElement = { getAttribute: () => null };
  assert.equal(await requestPublisherPdfs(['item-1']), false);
});

test('a selected-ID request waits for its own extension acknowledgement', async () => {
  globalThis.document = new EventTarget();
  document.documentElement = { getAttribute: () => 'pdfs' };
  const seen = [];
  document.addEventListener('synapse:publisher-pdfs', (event) => {
    const request = JSON.parse(event.detail);
    seen.push(request.ids);
    document.dispatchEvent(new CustomEvent('synapse:publisher-pdfs-result', {
      detail: JSON.stringify({ requestId: 'another-request', accepted: false }),
    }));
    document.dispatchEvent(new CustomEvent('synapse:publisher-pdfs-result', {
      detail: JSON.stringify({ requestId: request.requestId, accepted: true }),
    }));
  });
  assert.equal(await requestPublisherPdfs(['item-1', 'item-2']), true);
  assert.deepEqual(seen, [['item-1', 'item-2']]);
  assert.equal(await requestPublisherPdfs([]), false);
  assert.equal(await requestPublisherPdfs(Array(201).fill('item-1')), false);
});
