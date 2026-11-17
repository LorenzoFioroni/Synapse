import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MentionData, insertMention, mentionTrigger } from '../../frontend/js/ai/mentions.js';

test('only "[@" and "[[" open the list, on the line of the caret', () => {
  const t = (s) => mentionTrigger(s, s.length);
  assert.deepEqual(t('see [@smi'), { kind: 'cite', query: 'smi', from: 6, to: 9 });
  assert.deepEqual(t('a\nsee [[Rev'), { kind: 'note', query: 'Rev', from: 8, to: 11 });
  assert.deepEqual(t('x [@a; @b'), { kind: 'cite', query: 'b', from: 8, to: 9 });
  assert.equal(t('mail me @home'), null);        // a bare @ is an address or a handle
  assert.equal(t('done [@a]'), null);
  assert.equal(t('[[a]] then'), null);
  assert.equal(mentionTrigger('see [@smi', 3), null);   // the caret is before it
});

test('choosing completes the brackets and puts the caret after them', () => {
  const cite = mentionTrigger('see [@smi and more', 9);
  assert.deepEqual(insertMention('see [@smi and more', cite, 'smith2021'), { text: 'see [@smith2021] and more', caret: 16 });
  const note = mentionTrigger('[[Rev', 5);
  assert.deepEqual(insertMention('[[Rev', note, 'Review'), { text: '[[Review]]', caret: 10 });
  const closed = mentionTrigger('[[Rev]] x', 5);   // brackets already there: not doubled
  assert.deepEqual(insertMention('[[Rev]] x', closed, 'Review'), { text: '[[Review]] x', caret: 10 });
});

test('a [[note]] resolves by file name or path, shallowest first, once the notes are loaded', async () => {
  const notes = [{ path: 'deep/er/Review.md' }, { path: 'a/Review.md' }, { path: 'Other.md' }];
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ notes }) });
  const d = new MentionData();
  assert.equal(d.resolveNote('review'), undefined);   // not known yet; this starts the load
  await d.load('note');
  assert.equal(d.resolveNote('review'), 'a/Review.md');
  assert.equal(d.resolveNote('deep/er/Review'), 'deep/er/Review.md');
  assert.equal(d.resolveNote('nothing'), null);
});
