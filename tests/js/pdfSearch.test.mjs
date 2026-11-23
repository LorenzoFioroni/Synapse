import assert from 'node:assert/strict';
import { test } from 'node:test';
import { indexPage, pageMatches, PdfSearchIndex } from '../../frontend/js/viewer/searchModel.js';

const defaults = { matchCase: false, wholeWord: false };
const search = (items, query, options = defaults) => pageMatches(indexPage(items), 7, query, options);

test('literal, case-insensitive matches preserve punctuation and original item offsets', () => {
  assert.deepEqual(search([{ str: 'x-y xy x y X-Y' }], 'x-y'), [
    { page: 7, begin: { item: 0, offset: 0 }, end: { item: 0, offset: 3 } },
    { page: 7, begin: { item: 0, offset: 11 }, end: { item: 0, offset: 14 } },
  ]);
  assert.equal(search([{ str: 'x-y xy x y X-Y' }], 'x-y', { ...defaults, matchCase: true }).length, 1);
  assert.equal(search([{ str: 'a.b a*b' }], 'a.b').length, 1);
  assert.deepEqual(search([{ str: 'abc' }], '  '), []);
});

test('phrases cross item boundaries and line breaks, including empty PDF text items', () => {
  const items = [{ str: 'A quan' }, { str: '' }, { str: 'tum', hasEOL: true }, { str: 'state evolves.' }];
  assert.deepEqual(search(items, 'quantum state'), [
    { page: 7, begin: { item: 0, offset: 2 }, end: { item: 3, offset: 5 } },
  ]);
  assert.deepEqual(search(items, 'quantum'), [
    { page: 7, begin: { item: 0, offset: 2 }, end: { item: 2, offset: 3 } },
  ]);
});

test('line-end and soft hyphens join words; in-line hyphens remain meaningful', () => {
  const items = [{ str: 'trans-', hasEOL: true }, { str: 'port and co\u00adherent and x-y' }];
  assert.deepEqual(search(items, 'transport')[0], {
    page: 7, begin: { item: 0, offset: 0 }, end: { item: 1, offset: 4 },
  });
  assert.equal(search(items, 'coherent').length, 1);
  assert.equal(search(items, 'xy').length, 0);
});

test('ligatures, accents, Unicode and surrogate pairs map back to UTF-16 text offsets', () => {
  const items = [{ str: '😀 oﬃce café e\u0301tat 𝛙' }];
  assert.deepEqual(search(items, 'office')[0], {
    page: 7, begin: { item: 0, offset: 3 }, end: { item: 0, offset: 7 },
  });
  assert.equal(search(items, 'cafe').length, 1);
  assert.equal(search(items, 'état').length, 1);
  assert.equal(search(items, 'ψ').length, 1);
  assert.deepEqual(search(items, '😀')[0], {
    page: 7, begin: { item: 0, offset: 0 }, end: { item: 0, offset: 2 },
  });
});

test('whole words use Unicode boundaries and permit adjacent punctuation and whitespace', () => {
  assert.equal(search([{ str: 'state states (state) x state 😀state état états _state' }], 'state', { ...defaults, wholeWord: true }).length, 4);
  assert.equal(search([{ str: 'état états' }], 'etat', { ...defaults, wholeWord: true }).length, 1);
});

test('the document index searches far pages without rendering, caches extraction, and wraps from the current page', async () => {
  const read = [], seen = [];
  const index = new PdfSearchIndex(300, async page => {
    read.push(page);
    return [{ str: page === 250 ? 'The needle.' : '' }];
  });
  await index.search('needle', defaults, 249, () => false, (page, matches) => seen.push({ page, matches }));
  assert.deepEqual(seen.slice(0, 3).map(p => p.page), [249, 250, 251]);
  assert.equal(seen.at(-1).page, 248);
  assert.deepEqual(seen.filter(p => p.matches.length).map(p => p.page), [250]);
  await index.search('missing', defaults, 1, () => false, () => {});
  assert.equal(read.length, 300);
});

test('superseded extraction is cached but cannot publish old results or start another page', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  let stale = false;
  const read = [], old = [], fresh = [];
  const index = new PdfSearchIndex(2, page => {
    read.push(page);
    return page === 1 ? pending : Promise.resolve([{ str: 'second new' }]);
  });
  const first = index.search('old', defaults, 1, () => stale, (...r) => old.push(r));
  stale = true;
  const second = index.search('new', defaults, 1, () => false, (page, matches) => fresh.push([page, matches.length]));
  release([{ str: 'old new' }]);
  await Promise.all([first, second]);
  assert.deepEqual(old, []);
  assert.deepEqual(read, [1, 2]);
  assert.deepEqual(fresh, [[1, 1], [2, 1]]);
});

test('blank/scanned pages and failed extraction do not hide matches on later pages', async () => {
  let fail = true;
  const index = new PdfSearchIndex(3, async page => {
    if (page === 1 && fail) throw new Error('broken page');
    return page === 3 ? [{ str: 'needle' }] : [];
  });
  const seen = [];
  await index.search('needle', defaults, 1, () => false, (page, matches, failed) => seen.push([page, matches.length, failed]));
  assert.deepEqual(seen, [[1, 0, true], [2, 0, false], [3, 1, false]]);
  fail = false;
  const retry = [];
  await index.search('needle', defaults, 1, () => false, (page, matches, failed) => retry.push(failed));
  assert.deepEqual(retry, [false, false, false]);
});
