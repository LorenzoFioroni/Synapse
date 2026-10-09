import assert from 'node:assert/strict';
import { test } from 'node:test';
import { destTarget, popupPlacement, positionAt, previewOrigin, rectPercent, scrollFor } from '../../frontend/js/viewer/linkNav.js';

const ref = { num: 12, gen: 0 };

test('destTarget reads every destination kind', () => {
  assert.deepEqual(destTarget([ref, { name: 'XYZ' }, 72, 700, 0]), { ref, x: 72, y: 700 });
  assert.deepEqual(destTarget([ref, { name: 'XYZ' }, null, 500, null]), { ref, x: null, y: 500 });
  assert.deepEqual(destTarget([ref, { name: 'FitH' }, 640]), { ref, x: null, y: 640 });
  assert.deepEqual(destTarget([ref, { name: 'FitBV' }, 30]), { ref, x: 30, y: null });
  assert.deepEqual(destTarget([ref, { name: 'FitR' }, 10, 20, 300, 400]), { ref, x: 10, y: 400 });
  assert.deepEqual(destTarget([3, { name: 'Fit' }]), { ref: 3, x: null, y: null });
  assert.equal(destTarget('named'), null);
  assert.equal(destTarget([]), null);
});

test('rectPercent normalises the corner order', () => {
  assert.deepEqual(rectPercent([300, 100, 100, 50], 400, 200),
    { left: '25.0000%', top: '25.0000%', width: '50.0000%', height: '25.0000%' });
});

test('positionAt and scrollFor are inverses', () => {
  const pages = [{ num: 1, top: 20, height: 1000 }, { num: 2, top: 1040, height: 1000 }, { num: 3, top: 2060, height: 1000 }];
  assert.deepEqual(positionAt(pages, 0), { page: 1, frac: -0.02 });
  assert.deepEqual(positionAt(pages, 1540), { page: 2, frac: 0.5 });
  assert.equal(scrollFor(pages, positionAt(pages, 2310)), 2310);
  // after zooming ×2 the same position maps to the same place on the page
  const zoomed = pages.map((p, i) => ({ num: p.num, top: 20 + i * 2020, height: 2000 }));
  assert.equal(scrollFor(zoomed, { page: 2, frac: 0.5 }), 2040 + 1000);
  assert.equal(scrollFor(pages, { page: 9, frac: 0 }), null);
  assert.equal(positionAt([], 10), null);
});

test('popupPlacement prefers below, flips above, stays on screen', () => {
  assert.deepEqual(popupPlacement({ left: 100, top: 100, bottom: 120 }, 200, 100, 800, 600), { left: 100, top: 128, below: true });
  assert.deepEqual(popupPlacement({ left: 100, top: 550, bottom: 570 }, 200, 100, 800, 600), { left: 100, top: 442, below: false });
  assert.equal(popupPlacement({ left: 750, top: 10, bottom: 20 }, 200, 100, 800, 600).left, 596);
});

test('previewOrigin keeps the target near the top-left, inside the page', () => {
  assert.deepEqual(previewOrigin(300, 500, 460, 230, 826, 1069), { x: 284, y: 484 });
  assert.deepEqual(previewOrigin(700, 1050, 460, 230, 826, 1069), { x: 366, y: 839 });
  assert.deepEqual(previewOrigin(null, null, 460, 230, 826, 1069), { x: 0, y: 0 });
});
