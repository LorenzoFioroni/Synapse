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
