// Ask AI layout arithmetic: snapping the button, keeping sizes inside the window, resizing by dragging.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  clampDock, clampPopup, cornerAfterKey, innerCorner, maxDock, nearestCorner, resizedDock, resizedPopup,
} from '../../frontend/js/ai/layout.js';

const view = { w: 1000, h: 700 };

test('a released button snaps to the corner nearest its centre', () => {
  assert.equal(nearestCorner(100, 80, view), 'tl');
  assert.equal(nearestCorner(900, 80, view), 'tr');
  assert.equal(nearestCorner(100, 650, view), 'bl');
  assert.equal(nearestCorner(900, 650, view), 'br');
  assert.equal(nearestCorner(499, 349, view), 'tl');   // the middle belongs to the lower right
  assert.equal(nearestCorner(500, 350, view), 'br');
});

test('arrow keys move the button one step along an axis and stay at the edge', () => {
  assert.equal(cornerAfterKey('br', 'ArrowLeft'), 'bl');
  assert.equal(cornerAfterKey('br', 'ArrowUp'), 'tr');
  assert.equal(cornerAfterKey('tl', 'ArrowLeft'), 'tl');
  assert.equal(cornerAfterKey('tl', 'ArrowDown'), 'bl');
  assert.equal(cornerAfterKey('bl', 'a'), 'bl');
});

test('the popup is gripped at the corner that faces the middle', () => {
  assert.equal(innerCorner('br'), 'tl');
  assert.equal(innerCorner('tl'), 'br');
  assert.equal(innerCorner('bl'), 'tr');
  assert.equal(innerCorner('tr'), 'bl');
});

test('popup sizes are clamped to the minimum and the window', () => {
  assert.deepEqual(clampPopup({ w: 100, h: 100 }, view), { w: 300, h: 320 });
  assert.deepEqual(clampPopup({ w: 5000, h: 5000 }, view), { w: 976, h: 676 });
  assert.deepEqual(clampPopup({ w: 400, h: 500 }, view), { w: 400, h: 500 });
  // a window smaller than the minimum: the minimum wins, nothing negative
  assert.deepEqual(clampPopup({ w: 400, h: 500 }, { w: 200, h: 200 }), { w: 300, h: 320 });
});

test('dragging the inner corner grows the popup away from its anchor', () => {
  const start = { w: 390, h: 580 };
  // anchored lower right, grip at the top left: dragging up and left grows it
  assert.deepEqual(resizedPopup(start, 'br', -60, -40, view), { w: 450, h: 620 });
  assert.deepEqual(resizedPopup(start, 'br', 60, 40, view), { w: 330, h: 540 });
  // anchored upper left, grip at the lower right: dragging down and right grows it
  assert.deepEqual(resizedPopup(start, 'tl', 60, 40, view), { w: 450, h: 620 });
  assert.deepEqual(resizedPopup(start, 'bl', 60, -40, view), { w: 450, h: 620 });
  assert.deepEqual(resizedPopup(start, 'br', -2000, -2000, view), { w: 976, h: 676 });
  assert.deepEqual(resizedPopup(start, 'br', 2000, 2000, view), { w: 300, h: 320 });
});

test('the docked width has a minimum, a maximum and a share of the window', () => {
  assert.equal(maxDock({ w: 1000, h: 700 }), 600);
  assert.equal(maxDock({ w: 2000, h: 700 }), 640);
  assert.equal(maxDock({ w: 400, h: 700 }), 280);
  assert.equal(clampDock(100, view), 280);
  assert.equal(clampDock(900, view), 600);
  assert.equal(clampDock(400, view), 400);
  // a column on the right grows as its inner (left) edge moves left
  assert.equal(resizedDock(360, 'right', -80, view), 440);
  assert.equal(resizedDock(360, 'right', 200, view), 280);
  assert.equal(resizedDock(360, 'left', 80, view), 440);
});
