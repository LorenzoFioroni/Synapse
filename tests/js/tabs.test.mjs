// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
// The app's own tabs without a DOM (tabs/model.ts) and the viewer's Back/Forward inside a tab
// (viewer/jumpHistory.ts), and the place a new window opens at (windows.ts).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HOME, TabSet, noteTitle, reopenUrl, urlAtSpot, validSpec } from '../../frontend/js/tabs/model.js';
import { JumpHistory } from '../../frontend/js/viewer/jumpHistory.js';
import { placeHash, readPlace } from '../../frontend/js/windows.js';

const pdf = (n) => ({ key: `/pdfs/${n}.pdf`, url: `/viewer.html?file=%2Fpdfs%2F${n}.pdf&title=${n}`, title: n });
const titles = (s) => s.tabs.map((t) => t.title);
const activeTitle = (s) => (s.active === HOME ? HOME : s.get(s.active).title);

test('opening tabs: at the end, shown, one per PDF', () => {
  const s = new TabSet();
  assert.equal(s.active, HOME);
  const a = s.add(pdf('a'));
  s.add(pdf('b'));
  assert.deepEqual(titles(s), ['a', 'b']);
  assert.equal(activeTitle(s), 'b');
  assert.equal(s.byKey('/pdfs/a.pdf'), a);
  assert.equal(s.byKey('/pdfs/zzz.pdf'), null);
  const quiet = s.add(pdf('c'), { activate: false });
  assert.equal(activeTitle(s), 'b');
  assert.equal(quiet.opener, null);
});

test('of two tabs of one PDF, the one shown last is reused', () => {
  const s = new TabSet();
  const a1 = s.add(pdf('a'));
  const a2 = s.add(pdf('a'));
  assert.equal(s.byKey('/pdfs/a.pdf'), a2);
  s.activate(a1.id);
  assert.equal(s.byKey('/pdfs/a.pdf'), a1);
});

test('closing the active tab: its opener, else the right one, else the left one', () => {
  const s = new TabSet();
  const a = s.add(pdf('a'));            // opened from the library
  s.close(a.id);
  assert.equal(s.active, HOME, 'straight back to the library');

  const b = s.add(pdf('b'));
  const c = s.add(pdf('c'));            // opened from b
  s.close(c.id);
  assert.equal(s.active, b.id, 'back to b, which opened it');

  const d = s.add(pdf('d'));            // opened from b
  s.activate(HOME);                     // left d: the link to b is forgotten
  s.activate(d.id);
  s.close(d.id);
  assert.equal(s.active, b.id, 'no opener: the tab to the left (none to the right)');

  const e = s.add(pdf('e'));
  s.add(pdf('f'), { activate: false });
  s.activate(HOME);
  s.activate(e.id);
  s.close(e.id);
  assert.equal(activeTitle(s), 'f', 'the tab to the right first');
});

test('closing a tab that is not shown keeps the shown one', () => {
  const s = new TabSet();
  const a = s.add(pdf('a'));
  const b = s.add(pdf('b'));
  assert.equal(s.close(a.id), true);
  assert.equal(s.active, b.id);
  assert.equal(s.close('nope'), false);
});

test('a closed tab is forgotten as an opener', () => {
  const s = new TabSet();
  const a = s.add(pdf('a'));
  const b = s.add(pdf('b'));            // opened from a
  s.close(a.id);                        // not the active one
  assert.equal(b.opener, null);
  s.close(b.id);
  assert.equal(s.active, HOME);
});

test('close others, to the right, all; reopen the last closed', () => {
  const s = new TabSet();
  ['a', 'b', 'c', 'd'].forEach((n) => s.add(pdf(n)));
  const b = s.tabs[1];
  s.closeRight(b.id);
  assert.deepEqual(titles(s), ['a', 'b']);
  s.closeOthers(b.id);
  assert.deepEqual(titles(s), ['b']);
  assert.equal(s.active, b.id);
  const r = s.reopen();
  assert.equal(r.title, 'a', 'the last one closed comes back first');
  assert.deepEqual(titles(s), ['b', 'a']);
  assert.equal(s.active, r.id);
  s.closeAll();
  assert.deepEqual(titles(s), []);
  assert.equal(s.active, HOME);
  for (let i = 0; i < 20; i++) s.reopen();
  assert.equal(s.tabs.length, 4, 'every closed tab came back once');
  assert.equal(s.reopen(), null);
});

test('only the last ten closed tabs are kept', () => {
  const s = new TabSet();
  for (let i = 0; i < 15; i++) s.close(s.add(pdf(`p${i}`)).id);
  assert.equal(s.closed.length, 10);
  assert.equal(s.closed[0].title, 'p5');
});

test('moving, stepping round, the n-th tab', () => {
  const s = new TabSet();
  ['a', 'b', 'c'].forEach((n) => s.add(pdf(n)));
  const [a, b, c] = s.tabs;
  s.move(c.id, 0);
  assert.deepEqual(titles(s), ['c', 'a', 'b']);
  s.move(c.id, 99);
  assert.deepEqual(titles(s), ['a', 'b', 'c']);
  s.activate(c.id);
  assert.equal(s.step(1), HOME, 'after the last tab comes the Synapse tab');
  assert.equal(s.step(-1), b.id);
  s.activate(HOME);
  assert.equal(s.step(-1), c.id);
  assert.equal(s.nth(1), HOME);
  assert.equal(s.nth(2), a.id);
  assert.equal(s.nth(9), c.id, '9 is the last tab');
  assert.equal(s.nth(7), null);
});

test('saved and restored: order, addresses, the active tab', () => {
  const s = new TabSet();
  ['a', 'b', 'c'].forEach((n) => s.add(pdf(n)));
  s.activate(s.tabs[1].id);
  const saved = JSON.parse(JSON.stringify(s.save((t) => `${t.url}&page=4`)));
  assert.equal(saved.active, 1);
  const r = TabSet.restore(saved);
  assert.deepEqual(titles(r), ['a', 'b', 'c']);
  assert.equal(activeTitle(r), 'b');
  assert.ok(r.tabs[0].url.endsWith('&page=4'));
  s.activate(HOME);
  assert.equal(TabSet.restore(s.save()).active, HOME);
});

test('restoring ignores what does not look like saved tabs', () => {
  for (const bad of [null, 'x', 42, {}, { v: 2, tabs: [] }, { v: 1, tabs: 'no' }]) {
    const r = TabSet.restore(bad);
    assert.equal(r.tabs.length, 0);
    assert.equal(r.active, HOME);
  }
  const r = TabSet.restore({ v: 1, active: 1, tabs: [
    { key: 'k', url: 'https://evil.example/', title: 'x' },     // not the viewer
    { key: 'k2', url: '/viewer.html?file=a', title: 'ok' },
    null,
  ] });
  assert.deepEqual(titles(r), ['ok']);
  assert.equal(activeTitle(r), 'ok', 'the active index still points at the same tab');
  const r2 = TabSet.restore({ v: 1, active: 0, tabs: [{ key: 'k', url: 'javascript:alert(1)', title: 'x' }] });
  assert.equal(r2.active, HOME);
});

test('addresses: reopened at the page read, pointed at a new spot', () => {
  const u = '/viewer.html?file=%2Fpdfs%2Fa.pdf&page=3&q=some+words&rect=0.1%2C0.2%2C0.3%2C0.4';
  assert.equal(reopenUrl(u, 7), '/viewer.html?file=%2Fpdfs%2Fa.pdf&page=7');
  assert.equal(reopenUrl(u, 1), '/viewer.html?file=%2Fpdfs%2Fa.pdf');
  assert.equal(reopenUrl(u, null), '/viewer.html?file=%2Fpdfs%2Fa.pdf');
  assert.equal(urlAtSpot(u, { page: 5 }), '/viewer.html?file=%2Fpdfs%2Fa.pdf&page=5');
  assert.equal(urlAtSpot('/viewer.html?file=a', { page: 2, q: 'x y' }), '/viewer.html?file=a&page=2&q=x+y');
});

test('Back/Forward inside a PDF tab', () => {
  const h = new JumpHistory();
  assert.equal(h.canBack, false);
  assert.equal(h.back(), null);
  h.record({ page: 1, y: 0.5 });
  h.push({ page: 3, y: 0.1 }, '/viewer.html?page=3');
  assert.equal(h.canBack, true);
  assert.equal(h.canForward, false);
  h.record({ page: 3, y: 0.6 });         // scrolled on after the jump
  assert.deepEqual(h.back().pos, { page: 1, y: 0.5 });
  assert.equal(h.canForward, true);
  const fwd = h.forward();
  assert.deepEqual(fwd.pos, { page: 3, y: 0.6 }, 'forward returns to where the reader scrolled to');
  assert.equal(fwd.url, '/viewer.html?page=3');
  h.back();
  h.push({ page: 2 });                   // a new jump drops what was ahead
  assert.equal(h.canForward, false);
  assert.deepEqual(h.entries.map((e) => e.pos?.page), [1, 2]);
  h.record(null);                        // no position yet: nothing changes
  assert.deepEqual(h.entries[1].pos, { page: 2 });
});

test('the place a new window opens at: written and read back', () => {
  const pdf = '/viewer.html?file=%2Fpdfs%2Fa+b.pdf&title=A+%26+B&page=3';
  const round = (p) => readPlace(placeHash(p));
  assert.deepEqual(round({ pdf }), { pdf, fresh: true });
  assert.deepEqual(round({ note: 'Notes/Ideas & más.md', line: 12 }), { note: 'Notes/Ideas & más.md', line: 12, fresh: true });
  assert.deepEqual(round({ task: 'abc23456' }), { task: 'abc23456', fresh: true });
  assert.deepEqual(round({ item: 'x1', view: 'folder:4' }), { item: 'x1', view: 'folder:4', fresh: true });
  assert.deepEqual(round({}), { fresh: true }, 'New Window: just the library');
  assert.equal(placeHash({ note: 'a.md', line: null }), '#note=a.md&window=new');
});

test('reading a place leaves out what does not look like one', () => {
  assert.equal(readPlace(''), null);
  assert.equal(readPlace('#'), null);
  assert.equal(readPlace('#something=else'), null);
  assert.deepEqual(readPlace('#item=abc'), { item: 'abc' }, 'the browser extension: not a new window');
  assert.deepEqual(readPlace('#view=trash'), null);
  assert.deepEqual(readPlace('#view=folder:x'), null);
  assert.deepEqual(readPlace('#view=smart:7'), { view: 'smart:7' });
  assert.deepEqual(readPlace(`#pdf=${encodeURIComponent('https://evil.example/viewer.html?x')}`), null);
  assert.deepEqual(readPlace(`#pdf=${encodeURIComponent('javascript:alert(1)')}&window=new`), { fresh: true });
  assert.deepEqual(readPlace('#note=a.md&line=0'), { note: 'a.md' });
  assert.deepEqual(readPlace('#note=a.md&line=x'), { note: 'a.md' });
  assert.equal(placeHash({ view: 'trash', pdf: 'https://evil.example/' }), '#window=new');
});

const notes = (s) => s.tabs.filter((t) => t.kind === 'note').map((t) => `${t.preview ? '~' : ''}${t.title}`);

test('notes open VS Code\'s way: a preview tab the next note replaces', () => {
  const s = new TabSet();
  const a = s.openNote('Notes/A.md');
  assert.deepEqual(notes(s), ['~A']);
  assert.equal(s.active, a.id);
  const b = s.openNote('B.md');
  assert.equal(b, a, 'the same tab, now B');
  assert.deepEqual(notes(s), ['~B']);
  assert.equal(s.keep(b.id), true);
  assert.equal(s.keep(b.id), false, 'already kept');
  s.openNote('C.md');
  assert.deepEqual(notes(s), ['B', '~C'], 'a new preview tab once the old one is kept');
  s.openNote('B.md');
  assert.equal(activeTitle(s), 'B', 'a note with its own tab: that tab');
  assert.deepEqual(notes(s), ['B', '~C']);
  s.openNote('C.md', { keep: true });
  assert.deepEqual(notes(s), ['B', 'C'], 'keep: the tab it ends up in is kept');
  s.openNote('D.md', { keep: true });
  assert.deepEqual(notes(s), ['B', 'C', 'D'], 'keep with no preview tab: a new kept tab');
});

test('a new note tab goes right after the active tab', () => {
  const s = new TabSet();
  s.add(pdf('p1'));
  s.add(pdf('p2'));
  s.activate(s.tabs[0].id);
  s.openNote('A.md');
  assert.deepEqual(s.tabs.map((t) => t.title), ['p1', 'A', 'p2']);
  s.activate(HOME);
  s.openNote('B.md');
  assert.deepEqual(s.tabs.map((t) => t.title), ['p1', 'B', 'p2'], 'the preview tab keeps its place');
});

test('only one preview tab', () => {
  const s = new TabSet();
  s.add({ kind: 'note', key: 'A.md', preview: true });
  s.add({ kind: 'note', key: 'B.md', preview: true });
  assert.deepEqual(notes(s), ['A', '~B']);
});

test('Notes with nothing open, and the note tab used last', () => {
  const s = new TabSet();
  const empty = s.openNote(null, { last: true });
  assert.equal(empty.key, '');
  assert.equal(empty.title, 'Notes');
  assert.equal(empty.preview, true);
  s.openNote('A.md');
  assert.equal(s.tabs.length, 1, 'the first note fills the empty tab');
  s.keep(s.active);
  s.openNote('B.md', { keep: true });
  s.activate(s.tabs[0].id);
  s.activate(HOME);
  assert.equal(s.openNote(null, { last: true }).title, 'A');
  assert.equal(noteTitle(''), 'Notes');
  assert.equal(noteTitle('a/b/Café.MD'), 'Café');
});

test('PDF tabs are never previews, and keep a tab of their own per PDF', () => {
  const s = new TabSet();
  s.openNote('A.md');
  const p = s.add({ ...pdf('x'), preview: true });
  assert.equal(p.preview, false);
  assert.equal(s.byKey('/pdfs/x.pdf'), p);
  assert.equal(s.byKey('/pdfs/x.pdf', 'note'), null);
});

test('renamed notes: their tabs (and closed tabs) follow', () => {
  const s = new TabSet();
  s.openNote('Old/A.md', { keep: true });
  s.openNote('Old/B.md', { keep: true });
  s.close(s.tabs[1].id);
  s.renameNotes((p) => p.replace(/^Old\//, 'New/'));
  assert.deepEqual(s.tabs.map((t) => t.key), ['New/A.md']);
  assert.equal(s.closed[0].key, 'New/B.md');
  assert.equal(s.reopen().title, 'B');
});

test('deleted notes: their tabs close; the one shown becomes Notes with nothing open', () => {
  const s = new TabSet();
  s.openNote('A.md', { keep: true });
  s.openNote('B.md', { keep: true });
  s.openNote('C.md');                          // preview, shown
  s.activate(s.tabs[1].id);                    // B shown
  const closed = s.notesGone((p) => p === 'A.md' || p === 'B.md');
  assert.deepEqual(notes(s), ['~Notes'], 'B empty (and the preview now); A and the old preview C closed');
  assert.equal(closed.length, 2);
  assert.equal(s.get(s.active).key, '');
  s.openNote('D.md');
  assert.deepEqual(notes(s), ['~D'], 'the next note fills it');
});

test('reopening a closed note tab: kept, or its tab if open again', () => {
  const s = new TabSet();
  const a = s.openNote('A.md');                // preview
  s.close(a.id);
  const r = s.reopen();
  assert.deepEqual(notes(s), ['A'], 'kept');
  s.close(r.id);
  s.openNote('A.md', { keep: true });
  s.reopen();
  assert.deepEqual(notes(s), ['A'], 'not twice');
  const e = s.openNote(null);
  s.close(e.id);
  assert.equal(s.closed.length, 0, 'an empty Notes tab is not worth reopening');
});

test('note tabs are saved and restored with the PDF tabs', () => {
  const s = new TabSet();
  s.add(pdf('x'));
  s.openNote('A.md');
  const r = TabSet.restore(JSON.parse(JSON.stringify(s.save())));
  assert.deepEqual(r.tabs.map((t) => [t.kind, t.title, t.preview]), [['pdf', 'x', false], ['note', 'A', true]]);
  assert.equal(r.get(r.active).title, 'A');
});

test('what a tab dragged from another window may be', () => {
  assert.ok(validSpec({ kind: 'note', key: 'a/b.md', title: 'b' }));
  assert.ok(validSpec({ kind: 'note', key: '' }));
  assert.ok(validSpec({ kind: 'pdf', key: 'k', url: '/viewer.html?file=k' }));
  assert.ok(validSpec({ key: 'k', url: '/viewer.html?file=k', title: 'x' }), 'no kind: a PDF tab');
  assert.ok(!validSpec({ kind: 'note', key: 'evil.html' }));
  assert.ok(!validSpec({ kind: 'pdf', key: 'k', url: 'https://evil.example/' }));
  assert.ok(!validSpec({ kind: 'window', key: 'x' }));
  assert.ok(!validSpec({ kind: 'note', key: 3 }));
  assert.ok(!validSpec(null));
});

test('a note tab keeps its mode through a reload; the preview tab keeps it for the next note', () => {
  const s = new TabSet();
  const t = s.openNote('A.md');
  t.mode = 'read';
  s.openNote('B.md');
  assert.equal(s.get(s.active).mode, 'read', 'the mode is the tab\'s');
  const r = TabSet.restore(JSON.parse(JSON.stringify(s.save())));
  assert.equal(r.get(r.active).mode, 'read');
  const bad = TabSet.restore({ v: 1, active: 0, tabs: [{ kind: 'note', key: 'A.md', mode: 'weird' }] });
  assert.equal(bad.tabs[0].mode, undefined);
});

test('a note opened kept from an empty Notes tab fills it (no empty tab left behind)', () => {
  const s = new TabSet();
  s.openNote(null, { last: true });
  s.openNote('New.md', { keep: true });
  assert.deepEqual(notes(s), ['New']);
});
