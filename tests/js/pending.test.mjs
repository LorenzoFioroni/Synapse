import assert from 'node:assert/strict';
import { test } from 'node:test';
import { pendingStatus, withPendingStatus } from '../../frontend/js/ui/pending.js';

class Element {
  children = [];
  attributes = {};
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  setAttribute(key, value) { this.attributes[key] = value; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); }
}
const root = new Element();
globalThis.document = { createElement: () => new Element(), getElementById: () => root };

test('pending work stays visible until resolved, and independent notices do not clear each other', async () => {
  let resolve;
  const operation = withPendingStatus('Waiting for passkey…', () => new Promise(r => { resolve = r; }));
  const notice = root.children[0];
  assert.equal(notice.attributes.role, 'status');
  assert.equal(notice.children[1].textContent, 'Waiting for passkey…');
  const other = pendingStatus('Installing…');
  other.set('Reconnecting…');
  assert.equal(root.children[1].children[1].textContent, 'Reconnecting…');
  resolve(false); // cancellation is a normal completion
  assert.equal(await operation, false);
  assert.equal(root.children.length, 1);
  other.close();
  other.close();
  assert.equal(root.children.length, 0);
});

test('rejected and synchronously throwing operations always clear status and preserve errors', async () => {
  const error = new Error('offline');
  await assert.rejects(withPendingStatus('Checking…', () => Promise.reject(error)), e => e === error);
  assert.equal(root.children.length, 0);
  await assert.rejects(withPendingStatus('Checking…', () => { throw error; }), e => e === error);
  assert.equal(root.children.length, 0);
});
