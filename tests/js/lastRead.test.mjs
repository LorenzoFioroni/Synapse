import assert from 'node:assert/strict';
import { test } from 'node:test';
import { pdfKey, withPosition, worthReopening } from '../../frontend/js/viewer/lastRead.js';

test('a PDF is named by its attachment, its notes-PDF number, else its file', () => {
  assert.equal(pdfKey(new URLSearchParams('file=%2Fpdfs%2Fa.pdf&paper_id=X&att=12')), 'att:12');
  assert.equal(pdfKey(new URLSearchParams('file=%2Fapi%2Fnote-pdfs%2F4%2Ffile&npdf=4')), 'npdf:4');
  assert.equal(pdfKey(new URLSearchParams('file=%2Fpdfs%2Fa.pdf')), 'file:/pdfs/a.pdf');
  assert.equal(pdfKey(new URLSearchParams('title=x')), null);
});

test('the PDF read last becomes the newest entry, and the oldest beyond the limit go', () => {
  let book = {};
  for (const k of ['a', 'b', 'c']) book = withPosition(book, k, { page: 2, frac: 0 }, 3);
  book = withPosition(book, 'a', { page: 9, frac: 0.5 }, 3);   // read again: moves to the end
  assert.deepEqual(Object.keys(book), ['b', 'c', 'a']);
  assert.deepEqual(book.a, { page: 9, frac: 0.5 });
  book = withPosition(book, 'd', { page: 3, frac: 0 }, 3);
  assert.deepEqual(Object.keys(book), ['c', 'a', 'd']);
});

test('the very top of the first page is not worth reopening at', () => {
  assert.equal(worthReopening(null), false);
  assert.equal(worthReopening({ page: 1, frac: 0 }), false);
  assert.equal(worthReopening({ page: 1, frac: 0.4 }), true);
  assert.equal(worthReopening({ page: 5, frac: 0 }), true);
  assert.equal(worthReopening({ page: 0, frac: 0 }), false);
});
