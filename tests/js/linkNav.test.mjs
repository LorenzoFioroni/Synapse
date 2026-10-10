import assert from 'node:assert/strict';
import { test } from 'node:test';
import { destTarget, findCaption, isFloatDest, popupPlacement, positionAt, previewOrigin, rectPercent, scrollFor } from '../../frontend/js/viewer/linkNav.js';

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

test('previewOrigin keeps the destination near the top, inside the page', () => {
  assert.deepEqual(previewOrigin(300, 500, 460, 230, 826, 1069), { x: 0, y: 484 });
  assert.deepEqual(previewOrigin(700, 1050, 460, 230, 826, 1069), { x: 366, y: 839 });
  assert.deepEqual(previewOrigin(null, null, 460, 230, 826, 1069), { x: 0, y: 0 });
});

test('a bottom line (a figure caption) goes near the bottom of the window', () => {
  // caption bottom at 600: the window ends 16px below it, the figure above is in view
  assert.deepEqual(previewOrigin(100, 560, 460, 360, 826, 1069, 16, 600), { x: 0, y: 256 });
  assert.deepEqual(previewOrigin(100, 60, 460, 360, 826, 1069, 16, 100), { x: 0, y: 0 });
  for (const d of ['figure.3', 'figure.caption.7', 'table.2', 'subfigure.1.2', 'algocf.1', 'lstlisting.4', 'Figure.1']) assert.ok(isFloatDest(d), d);
  for (const d of ['section.2', 'cite.smith', 'equation.1.4', 'figures', null, [1, { name: 'XYZ' }]]) assert.ok(!isFloatDest(d), String(d));
});

// A two-column page (612 pt wide): text items {str, x, y, h} in PDF coordinates (y up).
const line = (str, x, y, h = 9) => ({ str, x, y, h });
const page = [
  line('Links: see Section I and', 54, 700), line('Fig. 1, Fig. 2 and Table I.', 54, 689),        // "Fig." starting a line of text
  line('I. SECTION ONE', 110, 660),
  line('FIG. 1. A wide figure with a caption', 54, 520), line('that spans two lines.', 54, 509),  // caption, left column
  line('Body text continues here', 54, 480),
  line('FIG. 2. A tall figure.', 330, 300),                                                       // right column
  line('TABLE I. A table.', 330, 150), line('a b', 400, 138),
];

test('findCaption: a destination at the end of a caption (most LaTeX figures)', () => {
  // anchors sit about a line above the text they mark: end of FIG. 1 is the line at y = 509
  const c = findCaption(page, 54, 509 + 9, 612);
  assert.ok(c && !c.table);
  assert.equal(c.top, 529);
  assert.ok(c.bottom < 509 && c.bottom > 500, String(c.bottom));
});

test('findCaption: a destination at the start of a caption, in the other column', () => {
  const c = findCaption(page, 400, 300 + 10, 612);
  assert.ok(c && Math.abs(c.bottom - 300) < 5);
  assert.equal(findCaption(page, 100, 300 + 10, 612), null);          // wrong column
});

test('findCaption: tables, and things that are not captions', () => {
  assert.equal(findCaption(page, 400, 150 + 9, 612).table, true);
  assert.equal(findCaption(page, 110, 660 + 9, 612), null);          // a section title below "Fig. 1, Fig. 2…"
  assert.equal(findCaption(page, 54, 480 + 9, 612), null);           // body text under a caption
  assert.equal(findCaption(page, 54, 698, 612), null);               // prose starting with "Fig. 1, …"
  assert.equal(findCaption([line('Fig. 3 shows the result.', 54, 300)], 54, 309, 612), null);
  assert.ok(findCaption([line('Figure 3 | Nature style', 54, 300)], 54, 309, 612));
  assert.ok(findCaption([line('TABLE II', 54, 300), line('. Elsewhere', 90, 300)], 54, 309, 612));
  assert.equal(findCaption(page, 54, null, 612), null);
});

test('previewOrigin centres the window on the destination\'s column', () => {
  // page 826 px wide (612 pt × 1.35), window 460 px
  assert.equal(previewOrigin(150, 300, 460, 260, 826, 1069).x, 0);      // a left-column title
  assert.equal(previewOrigin(505, 300, 460, 260, 826, 1069).x, 366);    // a right-column title
  assert.equal(previewOrigin(450, 300, 460, 260, 826, 1069).x, 366);
  assert.equal(previewOrigin(150, 300, 900, 260, 826, 1069).x, 0);      // window wider than the page
});
