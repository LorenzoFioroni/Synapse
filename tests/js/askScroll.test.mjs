import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Thread } from '../../frontend/js/ai/messages.js';

// Only geometry is supplied here; the browser checklist exercises actual layout and wheel/keyboard input.
class Element {
  textContent = '';
  classList = { toggle() {} };
  setAttribute() {}
  addEventListener() {}
  replaceChildren() {}
}
globalThis.document = { createElement: () => new Element() };

function fixture() {
  const thread = new Thread(), body = new Element(), parts = new Map();
  const box = new Element();
  box.querySelector = selector => {
    if (selector === '.ai-answer') return body;
    if (!parts.has(selector)) {
      const part = new Element();
      part.querySelector = () => new Element();
      parts.set(selector, part);
    }
    return parts.get(selector);
  };
  thread.el.clientHeight = 100;
  thread.el.scrollTop = 900;
  body.textContent = 'x'.repeat(1000);
  Object.defineProperty(thread.el, 'scrollHeight', { get: () => Math.max(100, body.textContent.length) });
  const turn = { box, text: body.textContent, tools: [], changes: [], phase: 'writing', state: 'running', error: '' };
  return { thread, turn };
}

test('a chunk larger than the viewport follows when already at the end', () => {
  const { thread, turn } = fixture();
  turn.text += 'x'.repeat(500);
  thread.draw(turn);
  assert.equal(thread.el.scrollTop, thread.el.scrollHeight);
});

test('scrolling up before a queued redraw keeps the reading position; returning resumes following', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { thread, turn } = fixture();
  turn.text += 'x'.repeat(500);
  thread.schedule(turn);
  thread.el.scrollTop = 650;
  t.mock.timers.tick(120);
  assert.equal(thread.el.scrollTop, 650);
  thread.el.scrollTop = thread.el.scrollHeight - thread.el.clientHeight;
  turn.text += 'x'.repeat(500);
  thread.schedule(turn);
  t.mock.timers.tick(120);
  assert.equal(thread.el.scrollTop, thread.el.scrollHeight);
});

for (const state of ['done', 'error', 'cancelled']) {
  test(`${state} preserves the reading position and includes the last pending chunk`, () => {
    const { thread, turn } = fixture();
    thread.el.scrollTop = 650;
    turn.text += 'final text';
    thread.schedule(turn);
    turn.state = state;
    thread.draw(turn);
    assert.equal(thread.el.scrollTop, 650);
    assert.equal(turn.box.querySelector('.ai-answer').textContent, turn.text);
  });
}

test('clearing a conversation discards its pending redraw', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { thread, turn } = fixture();
  const before = turn.text;
  turn.text += 'discarded';
  thread.schedule(turn);
  thread.clear();
  t.mock.timers.tick(120);
  assert.equal(turn.box.querySelector('.ai-answer').textContent, before);
});
