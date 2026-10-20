// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
// The app's own tabs without a DOM (tabs/model.ts) and the viewer's Back/Forward inside a tab
// (viewer/jumpHistory.ts).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HOME, TabSet, reopenUrl, urlAtSpot } from '../../frontend/js/tabs/model.js';
import { JumpHistory } from '../../frontend/js/viewer/jumpHistory.js';

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
