import assert from 'node:assert/strict';
import { test } from 'node:test';
import { History, LIMIT } from '../../frontend/js/notifications/model.js';
function fixture() {
  const disk = new Map();
  const persistence = { read: () => [...disk.values()], write: n => disk.set(n.id, n), remove: id => disk.delete(id) };
  return { disk, persistence, history: new History(persistence) };
}
const notice = (id, extra = {}) => ({ id: String(id), message: 'Papers ready', level: 'success', status: 'done', time: Number(id), read: false, ...extra });
test('history persists severity and independent desktop delivery; read/dismiss survive reload', () => {
  const { history: h, disk, persistence } = fixture();
  h.put(notice(1, { desktop: true, target: 'source:arxiv-today.today' }));
  assert.equal(h.unread, 1); h.markRead('1');
  const restored = new History(persistence);
  assert.equal(restored.unread, 0);
  assert.equal(restored.entries[0].level, 'success');
  assert.equal(restored.entries[0].desktop, true);
  restored.remove('1'); assert.equal(disk.size, 0);
});
test('history bounds completed notices while protecting running jobs and ignores corrupt records', () => {
  const { history: h, persistence, disk } = fixture();
  h.put(notice(0, { status: 'running' }));
  for (let i = 1; i < LIMIT + 12; i++) h.put(notice(i));
  assert.equal(h.entries.length, LIMIT + 1);
  h.remove('0'); assert.equal(h.entries.length, LIMIT + 1);
  disk.set('invalid', { id: 'invalid', time: 1 });
  const restored = new History(persistence);
  assert.equal(restored.entries.length, LIMIT + 1);
  restored.clear(); assert.equal(restored.entries.length, 1);
});
test('separate keys preserve another window’s notice and only interrupt this owner’s jobs', () => {
  const { history: a, persistence } = fixture(); const b = new History(persistence);
  a.put(notice(1, { status: 'running', owner: 'a' }));
  b.put(notice(2, { status: 'running', owner: 'b' }));
  a.reload(); a.interrupt('a'); b.reload();
  assert.equal(b.entries.length, 2);
  assert.equal(b.entries.find(n => n.id === '1').status, 'interrupted');
  assert.equal(b.entries.find(n => n.id === '2').status, 'running');
});
test('recovery interrupts abandoned and legacy jobs, preserving live windows and completed results', () => {
  const { history: h, persistence } = fixture();
  h.put(notice(1, { status: 'running', owner: 'closed-window', read: true }));
  h.put(notice(2, { status: 'running', owner: 'live-window' }));
  h.put(notice(3, { status: 'running' }));
  h.put(notice(4));
  const restored = new History(persistence);
  restored.recover(new Set(['live-window']), new Set(restored.entries.map(n => n.id)));
  const again = new History(persistence);
  for (const id of ['1', '3']) {
    const n = again.entries.find(n => n.id === id);
    assert.equal(n.status, 'interrupted');
    assert.equal(n.level, 'warning');
    assert.equal(n.read, false);
    assert.match(n.message, /check the result before retrying/);
  }
  assert.equal(again.entries.find(n => n.id === '2').status, 'running');
  assert.equal(again.entries.find(n => n.id === '4').status, 'done');
  again.clear();
  assert.deepEqual(again.entries.map(n => n.id), ['2']);
});
test('recovery does not overwrite a concurrent completion or a newly started job', () => {
  const { history: h, persistence } = fixture();
  h.put(notice(1, { status: 'running', owner: 'closed-window' }));
  const candidates = new Set(h.entries.map(n => n.id));
  const other = new History(persistence);
  other.put(notice(1, { message: 'Finished while checking owners' }));
  other.put(notice(2, { status: 'running', owner: 'new-window' }));
  h.reload();
  h.recover(new Set(), candidates);
  assert.equal(h.entries.find(n => n.id === '1').status, 'done');
  assert.equal(h.entries.find(n => n.id === '2').status, 'running');
});
test('explicit fallback dismissal removes one stranded job without clearing other running jobs', () => {
  const { history: h, persistence } = fixture();
  h.put(notice(1, { status: 'running', owner: 'gone' }));
  h.put(notice(2, { status: 'running', owner: 'live' }));
  h.remove('1', true, true);
  const restored = new History(persistence);
  restored.clear();
  assert.deepEqual(restored.entries.map(n => n.id), ['2']);
});
