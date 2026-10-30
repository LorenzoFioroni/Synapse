// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { NoteSession, draftStorageKey } from '../../frontend/js/views/notes/noteSession.js';
import { compactDiff, lineDiff } from '../../frontend/js/views/notes/diff.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const hashOf = (t) => `h:${t}`;

/** A fake server with one file, plus a fake localStorage. */
function setup(initial = 'v1', { delay = 20 } = {}) {
  const server = { text: initial, hash: hashOf(initial), down: false, saves: 0, deleted: false };
  const api = {
    async save(path, text, hash) {
      if (server.down) throw Object.assign(new Error('offline'), { status: 0 });
      if (server.deleted) throw Object.assign(new Error('gone'), { status: 409 });
      if (hash !== server.hash) throw Object.assign(new Error('conflict'), { status: 409 });
      await sleep(5);
      server.text = text; server.hash = hashOf(text); server.saves++;
      return { hash: server.hash };
    },
    async create(path, text) { server.deleted = false; server.text = text; server.hash = hashOf(text); return { hash: server.hash }; },
    async get() {
      if (server.deleted) throw Object.assign(new Error('nf'), { status: 404 });
      return { text: server.text, hash: server.hash };
    },
  };
  const mem = new Map();
  const drafts = { get: (k) => mem.get(k) ?? null, set: (k, v) => mem.set(k, v), remove: (k) => mem.delete(k) };
  const events = { statuses: [], conflicts: [], remote: [] };
  const s = new NoteSession({
    path: 'a.md', text: server.text, hash: server.hash, api, drafts, delay,
    onStatus: (st) => events.statuses.push(st), onConflict: (c) => events.conflicts.push(c),
    onRemoteText: (t) => events.remote.push(t),
  });
  return { server, api, drafts, mem, events, s };
}

// Observe the public state transition: a fixed sleep races the save's nested timer on busy CI.
function nextStatus(session, wanted) {
  return new Promise((resolve) => {
    const previous = session.onStatus;
    session.onStatus = (status, current) => {
      previous(status, current);
      if (status === wanted) { session.onStatus = previous; resolve(); }
    };
  });
}

test('autosave after the delay, draft cleared afterwards', { timeout: 2000 }, async () => {
  const { server, mem, s } = setup();
  const saved = nextStatus(s, 'saved');
  s.update('v2');
  assert.equal(s.status, 'dirty');
  assert.equal(mem.get(draftStorageKey('a.md')).text, 'v2');
  await saved;
  assert.equal(server.text, 'v2');
  assert.equal(s.status, 'saved');
  assert.equal(mem.size, 0);
});

test('typing during a save is saved too, and the status ends as saved', { timeout: 2000 }, async () => {
  const { server, s, events } = setup('v1', { delay: 5 });
  const saved = nextStatus(s, 'saved');
  s.update('a');
  const p = s.save();
  s.update('ab');            // while the first save is in flight
  await p;
  await saved;
  assert.equal(server.text, 'ab');
  assert.equal(s.status, 'saved');
  assert.equal(events.statuses.at(-1), 'saved');
});

test('undoing back to the saved text cancels the save', async () => {
  const { server, s, mem } = setup();
  s.update('v2');
  s.update('v1');
  await sleep(50);
  assert.equal(server.saves, 0);
  assert.equal(s.status, 'saved');
  assert.equal(mem.size, 0);
});

test('a change made elsewhere causes a conflict, never an overwrite', async () => {
  const { server, s, events, mem } = setup();
  server.text = 'other tab'; server.hash = hashOf('other tab');
  s.update('mine');
  await s.save();
  assert.equal(server.text, 'other tab');
  assert.equal(s.status, 'conflict');
  assert.deepEqual(events.conflicts[0], { mine: 'mine', theirs: 'other tab', theirsHash: hashOf('other tab'), deleted: false });
  s.update('mine 2');        // keep typing: no save, but the draft follows
  await sleep(50);
  assert.equal(server.text, 'other tab');
  assert.equal(mem.get(draftStorageKey('a.md')).text, 'mine 2');
});

test('resolve: keep mine overwrites, take theirs replaces the editor', async () => {
  let { server, s } = setup();
  server.text = 'theirs'; server.hash = hashOf('theirs');
  s.update('mine');
  await s.save();
  await s.resolve('mine');
  assert.equal(server.text, 'mine');
  assert.equal(s.status, 'saved');

  ({ server, s } = setup());
  const ev = [];
  s.onRemoteText = (t) => ev.push(t);
  server.text = 'theirs'; server.hash = hashOf('theirs');
  s.update('mine');
  await s.save();
  await s.resolve('theirs');
  assert.deepEqual(ev, ['theirs']);
  assert.equal(s.text, 'theirs');
  assert.equal(s.status, 'saved');
  s.update('theirs + more');
  await s.save();
  assert.equal(server.text, 'theirs + more');
});

test('identical edits on both sides are not a conflict', async () => {
  const { server, s } = setup();
  server.text = 'same'; server.hash = hashOf('same');
  s.update('same');
  await s.save();
  assert.equal(s.status, 'saved');
});

test('note deleted elsewhere: keep mine writes it again', async () => {
  const { server, s, events } = setup();
  server.deleted = true;
  s.update('mine');
  await s.save();
  assert.equal(events.conflicts[0].deleted, true);
  await s.resolve('mine');
  assert.equal(server.deleted, false);
  assert.equal(server.text, 'mine');
  assert.equal(s.status, 'saved');
});

test('the file could not be read when the conflict began: it is read again before use', async () => {
  const { server, api, s, events } = setup();
  const get = api.get;
  let reads = 0;
  api.get = async (...a) => { if (reads++ < 2) throw Object.assign(new Error('server error'), { status: 500 }); return get(...a); };
  server.text = 'theirs'; server.hash = hashOf('theirs');
  s.update('mine');
  await s.save();
  assert.deepEqual(events.conflicts[0], { mine: 'mine', theirs: null, theirsHash: null, deleted: false });
  // still unreadable: nothing changes, the session stays in conflict
  await assert.rejects(s.resolve('theirs'), /server error/);
  assert.equal(s.status, 'conflict');
  assert.ok(s.conflict);
  assert.deepEqual(events.remote, []);
  assert.equal(s.text, 'mine');
  // readable again: "use the saved file" loads the file, never null
  await s.resolve('theirs');
  assert.deepEqual(events.remote, ['theirs']);
  assert.equal(s.text, 'theirs');
  assert.equal(s.status, 'saved');
  assert.equal(server.text, 'theirs');
});

test('the file could not be read, then keep mine saves over its current version', async () => {
  const { server, api, s } = setup();
  const get = api.get;
  let reads = 0;
  api.get = async (...a) => { if (reads++ < 1) throw Object.assign(new Error('server error'), { status: 500 }); return get(...a); };
  server.text = 'theirs'; server.hash = hashOf('theirs');
  s.update('mine');
  await s.save();
  await s.resolve('mine');
  assert.equal(server.text, 'mine');
  assert.equal(s.status, 'saved');
});

test('note deleted elsewhere: if writing it again fails, it stays in conflict', async () => {
  const { server, api, s, mem } = setup();
  const create = api.create;
  api.create = async () => { throw Object.assign(new Error('offline'), { status: 0 }); };
  server.deleted = true;
  s.update('mine');
  await s.save();
  await assert.rejects(s.resolve('mine'), /offline/);
  assert.equal(s.status, 'conflict');
  assert.equal(s.conflict.deleted, true);
  assert.equal(mem.get(draftStorageKey('a.md')).text, 'mine');   // the text is safe meanwhile
  s.update('mine 2');
  await sleep(50);
  assert.equal(server.deleted, true);                              // still never saved behind the user's back
  api.create = create;
  await s.resolve('mine');
  assert.equal(server.text, 'mine 2');
  assert.equal(s.status, 'saved');
});

test('note deleted elsewhere and written again by someone else meanwhile: a conflict with their text', async () => {
  const { server, api, s, events } = setup();
  server.deleted = true;
  s.update('mine');
  await s.save();
  api.create = async () => { throw Object.assign(new Error('exists'), { status: 409 }); };
  server.deleted = false; server.text = 'recreated'; server.hash = hashOf('recreated');
  await s.resolve('mine');
  assert.equal(s.status, 'conflict');
  assert.deepEqual(events.conflicts.at(-1), { mine: 'mine', theirs: 'recreated', theirsHash: hashOf('recreated'), deleted: false });
  await s.resolve('mine');
  assert.equal(server.text, 'mine');
});

test('server down: offline with retry, then saved; the draft survives meanwhile', async () => {
  const { server, s, mem } = setup('v1', { delay: 5 });
  server.down = true;
  s.update('v2');
  await sleep(20);
  assert.equal(s.status, 'offline');
  assert.equal(mem.get(draftStorageKey('a.md')).text, 'v2');
  server.down = false;
  await sleep(2300);          // first retry after 2 s
  assert.equal(server.text, 'v2');
  assert.equal(s.status, 'saved');
  assert.equal(mem.size, 0);
});

test('opening with drafts: same text, restorable, or conflicting', () => {
  const mem = new Map();
  const drafts = { get: (k) => mem.get(k) ?? null, set: (k, v) => mem.set(k, v), remove: (k) => mem.delete(k) };
  const key = draftStorageKey('a.md');
  assert.deepEqual(NoteSession.open('a.md', { text: 't', hash: 'h1' }, drafts), { text: 't', restored: false, conflict: false });
  mem.set(key, { text: 't', baseHash: 'h0' });
  assert.equal(NoteSession.open('a.md', { text: 't', hash: 'h1' }, drafts).restored, false);
  assert.equal(mem.size, 0);    // a draft equal to the file is just removed
  mem.set(key, { text: 'unsaved', baseHash: 'h1' });
  assert.deepEqual(NoteSession.open('a.md', { text: 't', hash: 'h1' }, drafts), { text: 'unsaved', restored: true, conflict: false });
  mem.set(key, { text: 'unsaved', baseHash: 'old' });
  assert.deepEqual(NoteSession.open('a.md', { text: 't', hash: 'h1' }, drafts), { text: 'unsaved', restored: true, conflict: true });
});

test('checkRemote refreshes clean text and prompts for unsaved edits', async () => {
  const { server, s, events } = setup();
  server.text = 'remote'; server.hash = hashOf('remote');
  await s.checkRemote();
  assert.deepEqual(events.remote, ['remote']);
  assert.equal(s.saved.hash, hashOf('remote'));
  s.update('local');
  server.text = 'remote2'; server.hash = hashOf('remote2');
  await s.checkRemote();
  assert.deepEqual(events.remote, ['remote']);
  assert.equal(s.status, 'conflict');
  assert.equal(s.text, 'local');
  assert.equal(events.conflicts.at(-1).theirs, 'remote2');
  s.dispose();
});

test('checkRemote sends the hash it has and fetches the text only when it changed', async () => {
  const { server, api, s, events } = setup();
  const calls = { check: 0, get: 0 };
  const get = api.get;
  api.get = async (...a) => { calls.get++; return get(...a); };
  api.check = async (path, hash) => {
    calls.check++;
    return hash === server.hash ? { hash } : { text: server.text, hash: server.hash };
  };
  await s.checkRemote();
  await s.checkRemote();
  assert.deepEqual(calls, { check: 2, get: 0 });
  assert.deepEqual(events.remote, []);
  server.text = 'remote'; server.hash = hashOf('remote');
  await s.checkRemote();
  assert.deepEqual(events.remote, ['remote']);
  assert.equal(s.saved.hash, hashOf('remote'));
  // a server that leaves the text out although the hash differs: ask for the whole note
  api.check = async () => ({ hash: 'h:other' });
  server.text = 'other'; server.hash = 'h:other';
  await s.checkRemote();
  assert.deepEqual(events.remote, ['remote', 'other']);
  s.dispose();
});

test('lineDiff', () => {
  const rows = lineDiff('a\nb\nc\nd', 'a\nB\nc\nd\ne');
  assert.deepEqual(rows, [{ op: ' ', text: 'a' }, { op: '-', text: 'b' }, { op: '+', text: 'B' }, { op: ' ', text: 'c' },
    { op: ' ', text: 'd' }, { op: '+', text: 'e' }]);
  assert.deepEqual(lineDiff('x', 'x'), [{ op: ' ', text: 'x' }]);
  const long = Array.from({ length: 50 }, (_, i) => `l${i}`).join('\n');
  const c = compactDiff(lineDiff(long, long.replace('l25', 'changed')), 1);
  assert.deepEqual(c.map((r) => r.op), ['…', ' ', '-', '+', ' ', '…']);
});

for (const dirty of [false, true]) {
  test(`outside deletion preserves the open note (dirty=${dirty})`, async () => {
    const { server, s, events, mem } = setup();
    if (dirty) s.update('my draft');
    server.deleted = true;
    await s.checkRemote();
    assert.equal(s.status, 'conflict');
    assert.equal(events.conflicts.at(-1).deleted, true);
    assert.equal(mem.get(draftStorageKey('a.md')).text, dirty ? 'my draft' : 'v1');
    s.dispose();
  });
}

test('unchanged remote text does not interrupt unsaved typing', async () => {
  const { s, events } = setup();
  s.update('draft');
  await s.checkRemote();
  assert.equal(s.status, 'dirty');
  assert.equal(events.conflicts.length, 0);
  s.dispose();
});

test('typing while a remote read is pending is preserved', async () => {
  const { s, api, events } = setup();
  let answer;
  api.get = () => new Promise((resolve) => { answer = resolve; });
  const check = s.checkRemote();
  s.update('draft');
  api.get = async () => ({ text: 'outside', hash: hashOf('outside') });
  answer(await api.get());
  await check;
  assert.equal(s.text, 'draft');
  assert.equal(s.status, 'conflict');
  assert.deepEqual(events.remote, []);
  s.dispose();
});


test('overlapping remote checks show only one conflict dialog', async () => {
  const { s, server, events } = setup();
  s.update('draft');
  server.text = 'outside'; server.hash = hashOf('outside');
  await Promise.all([s.checkRemote(), s.checkRemote()]);
  assert.equal(events.conflicts.length, 1);
  s.dispose();
});

/** The note was renamed elsewhere to b.md: the old path answers 404 with moved_to. */
function moveTo(server, api, { changedAfter = false } = {}) {
  const moved = { text: changedAfter ? 'edited after the move' : server.text };
  moved.hash = hashOf(moved.text);
  server.deleted = true;
  const get = api.get;
  api.get = async (path) => {
    if (path === 'b.md') return { text: moved.text, hash: moved.hash };
    try { return await get(path); } catch (e) { throw Object.assign(e, { data: { moved_to: 'b.md' } }); }
  };
}

for (const dirty of [false, true]) {
  test(`a note moved elsewhere is followed, keeping the text (dirty=${dirty})`, async () => {
    const { server, api, s, events, mem } = setup();
    const follows = [];
    s.onMoved = (to, text) => { follows.push([to, text]); return true; };
    if (dirty) s.update('my draft');
    moveTo(server, api);
    await s.checkRemote();
    assert.deepEqual(follows, [['b.md', dirty ? 'my draft' : 'v1']]);
    assert.equal(events.conflicts.length, 0);
    assert.equal(mem.get(draftStorageKey('a.md')), undefined);
    await s.save();   // the old session never writes to the old path again
    assert.equal(server.saves, 0);
    s.dispose();
  });
}

test('a save after a move elsewhere follows instead of a deleted-note conflict', async () => {
  const { server, api, s, events } = setup('v1', { delay: 5 });
  const follows = [];
  s.onMoved = (to, text) => { follows.push([to, text]); return true; };
  moveTo(server, api);
  s.update('typed');
  await s.save();
  assert.deepEqual(follows, [['b.md', 'typed']]);
  assert.equal(events.conflicts.length, 0);
  s.dispose();
});

test('unsaved text and a note changed after its move elsewhere: the usual conflict', async () => {
  const { server, api, s, events } = setup();
  const follows = [];
  s.onMoved = (to) => { follows.push(to); return true; };
  s.update('my draft');
  moveTo(server, api, { changedAfter: true });
  await s.checkRemote();
  assert.deepEqual(follows, []);
  assert.equal(s.status, 'conflict');
  assert.equal(events.conflicts.at(-1).deleted, true);
  s.dispose();
});

test('a move the window cannot follow falls back to the deleted-note conflict', async () => {
  const { server, api, s, events } = setup();
  s.onMoved = () => false;
  moveTo(server, api);
  await s.checkRemote();
  assert.equal(s.status, 'conflict');
  assert.equal(events.conflicts.at(-1).deleted, true);
  s.dispose();
});
