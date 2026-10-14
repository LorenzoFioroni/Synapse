import assert from 'node:assert/strict';
import { test } from 'node:test';
import { state } from '../../frontend/js/store.js';
import { LookupJob } from '../../frontend/js/views/metadataReview.js';

state.schema = { fields: [{ key: 'title', editable: true }, { key: 'year', editable: true }], itemTypes: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test('looks up several items at a time, keeps their order, reports each result', async () => {
  let inFlight = 0, most = 0;
  const lookup = async (id) => {
    inFlight++; most = Math.max(most, inFlight);
    await sleep(40);
    inFlight--;
    if (id === 'c') throw Object.assign(new Error('not found'), { status: 404 });
    return { current: { id, title: 'T', year: 2020 }, fetched: { title: id === 'a' ? 'New title' : 'T' } };
  };
  const ids = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
  const job = new LookupJob(ids, { lookup, items: (id) => ({ id, title: 'T' }) });
  const seen = [];
  job.on((a) => seen.push(a ? a.id : 'END'));
  const t0 = Date.now();
  await job.start();
  assert.ok(most > 1 && most <= 4, `concurrency ${most}`);
  assert.ok(Date.now() - t0 < 8 * 40, 'faster than one by one');
  assert.equal(seen.at(-1), 'END');
  assert.deepEqual(job.articles.map((a) => a.status), ['changes', 'same', 'error', 'same', 'same', 'same', 'same', 'same']);
  assert.equal(job.withChanges, 1);
  assert.equal(job.count('same'), 6);
  assert.equal(job.looked, 8);
  assert.equal(job.articles[0].changes[0].key, 'title');
});

test('stop leaves the rest unlooked, and an unreachable server stops the job', async () => {
  const job = new LookupJob(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i'], {
    lookup: async (id) => { await sleep(10); return { current: { id }, fetched: {} }; }, items: (id) => ({ id }),
  });
  const p = job.start();
  job.stop();
  await p;
  assert.ok(job.count('skipped') >= 5);
  assert.equal(job.running, false);

  const down = new LookupJob(['a', 'b', 'c', 'd', 'e', 'f'], {
    lookup: async () => { throw Object.assign(new Error('Cannot reach the server'), { status: 0 }); }, items: (id) => ({ id }),
  });
  await down.start();
  assert.ok(down.count('error') <= 4 && down.count('skipped') >= 2);
});

test('applying queues the reference lists of looked-up articles only when asked, and only once', async () => {
  const lookup = async (id) => {
    if (id === 'c') throw new Error('not found');
    return { current: { id, title: 'T' }, fetched: { title: id === 'a' ? 'New title' : 'T' } };
  };
  const items = (id) => ({ id, title: 'T' });
  const off = new LookupJob(['a', 'b'], { lookup, items });
  await off.start();
  assert.deepEqual(off.referencesToQueue(), []);   // e.g. new items with references set to never

  // after an import: 'b' had a DOI and was queued when it was added
  const job = new LookupJob(['a', 'b', 'c'], { lookup, items, references: true, referencesQueued: ['b'] });
  await job.start();
  assert.deepEqual(job.referencesToQueue(), ['a']);
  assert.deepEqual(job.referencesToQueue(job.articles.slice(1)), []);
});
