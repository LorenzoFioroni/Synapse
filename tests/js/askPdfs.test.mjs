// Ask AI and PDFs of the notes folders: chips, suggestions, and how [[file.pdf]] links in a question or an answer resolve.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { describe, emptyTitle, suggestions } from '../../frontend/js/ai/context.js';
import { MentionData, insertMention, mentionTrigger } from '../../frontend/js/ai/mentions.js';

test('a PDF of the notes folders is a chip, with its page and words', () => {
  const page = describe({ kind: 'notepdf', path: 'refs/My Paper.pdf', page: 4 });
  assert.equal(page.label, 'My Paper, p. 4');
  assert.equal(page.icon, 'pdf');
  const words = describe({ kind: 'notepdf', path: 'refs/My Paper.pdf', page: 4, quote: 'the exact  words' });
  assert.equal(words.label, '“the exact words”');
  assert.match(words.title, /My Paper, p\. 4/);
  assert.equal(emptyTitle([page]), 'Ask about this PDF');
  assert.ok(suggestions([words]).includes('Explain this passage'));
  assert.ok(suggestions([page]).includes('Explain this page'));
});

test('[[ links to PDFs resolve by path or file name; unknown ones do not', () => {
  const data = new MentionData();
  data.noteList = [{ path: 'Ideas.md', title: 'Ideas' }];
  data.pdfList = [{ id: 1, path: 'refs/paper.pdf', name: 'paper.pdf' }, { id: 2, path: 'a/b/paper.pdf', name: 'paper.pdf' }];
  assert.equal(data.resolveNote('refs/paper.pdf'), 'refs/paper.pdf');
  assert.equal(data.resolveNote('PAPER.pdf'), 'refs/paper.pdf');   // the shortest path wins
  assert.equal(data.resolveNote('b/paper.pdf'), 'a/b/paper.pdf');
  assert.equal(data.resolveNote('gone.pdf'), null);
  assert.equal(data.resolveNote('ideas'), 'Ideas.md');
  assert.equal(data.findPdf('refs/paper.pdf').id, 1);
});

test('typing [[ offers a PDF name and completes it with its extension', () => {
  const t = mentionTrigger('see [[pap', 9);
  assert.equal(t.kind, 'note');
  assert.deepEqual(insertMention('see [[pap', t, 'paper.pdf'), { text: 'see [[paper.pdf]]', caret: 17 });
});
