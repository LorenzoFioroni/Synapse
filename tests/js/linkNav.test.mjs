import assert from 'node:assert/strict';
import { test } from 'node:test';
import { destTarget, figureRegion, findCaption, fitRegion, graphicsBoxes, isFloatDest, popupPlacement, positionAt, previewOrigin, rectPercent, scrollFor } from '../../frontend/js/viewer/linkNav.js';

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

// pdf.js operator codes used by graphicsBoxes (pdfjsLib.OPS)
const OPS = { save: 10, restore: 11, transform: 12, stroke: 20, closeStroke: 21, fill: 22, eoFill: 23, fillStroke: 24, eoFillStroke: 25,
  closeFillStroke: 26, closeEOFillStroke: 27, endPath: 28, clip: 29, eoClip: 30, shadingFill: 62, paintFormXObjectBegin: 74, paintFormXObjectEnd: 75,
  paintImageMaskXObject: 83, paintImageMaskXObjectGroup: 84, paintImageXObject: 85, paintInlineImageXObject: 86,
  paintInlineImageXObjectGroup: 87, paintImageXObjectRepeat: 88, paintImageMaskXObjectRepeat: 89, paintSolidColorImageMask: 90, constructPath: 91 };

test('graphicsBoxes: images under the current transform, painted paths, not clips', () => {
  const fn = [], args = [];
  const op = (f, a = null) => { fn.push(f); args.push(a); };
  op(OPS.save); op(OPS.transform, [200, 0, 0, 100, 50, 400]); op(OPS.paintImageXObject, ['img1']); op(OPS.restore);
  // paths as pdf.js ≥ 5 lists them: [paint op, [path data], [minX, minY, maxX, maxY]], a clip just before
  const path = (paint, minX, minY, maxX, maxY) => op(OPS.constructPath, [paint, [new Float32Array(0)], new Float32Array([minX, minY, maxX, maxY])]);
  op(OPS.clip); path(OPS.endPath, 0, 0, 612, 792);                                                  // a clip: no box
  op(OPS.paintFormXObjectBegin, [[1, 0, 0, 1, 300, 100], [0, 0, 100, 100]]);
  path(OPS.fill, 10, 0, 60, 80);                                                                     // inside a form
  op(OPS.paintFormXObjectEnd);
  path(OPS.stroke, 0, 0, 5, 5);
  op(OPS.constructPath, [OPS.fill, [null], null]);                                                   // an empty path
  assert.deepEqual(graphicsBoxes(fn, args, OPS), [[50, 400, 250, 500], [310, 100, 360, 180], [0, 0, 5, 5]]);
  // a clip cuts what is painted after it, until the restore
  const fn2 = [], args2 = [];
  const op2 = (f, a = null) => { fn2.push(f); args2.push(a); };
  op2(OPS.save); op2(OPS.clip); op2(OPS.constructPath, [OPS.endPath, [null], [0, 0, 100, 100]]);
  op2(OPS.constructPath, [OPS.fill, [null], [50, 50, 200, 200]]); op2(OPS.restore);
  op2(OPS.constructPath, [OPS.fill, [null], [150, 150, 200, 200]]);
  assert.deepEqual(graphicsBoxes(fn2, args2, OPS), [[50, 50, 100, 100], [150, 150, 200, 200]]);
});

test('figureRegion: the drawing above a caption, grown upwards, in its column, with its labels', () => {
  const boxes = [
    [60, 520, 290, 700],            // the figure's image, ends just above the caption (top 512)
    [60, 705, 290, 760],            // a second panel right above it
    [60, 300, 290, 320],            // something far below: not part of it
    [330, 520, 560, 700],           // the other column
  ];
  const caption = { top: 512, bottom: 488, x: 54, table: false };
  const texts = [{ x: 54, y: 500, w: 250 }, { x: 40, y: 740, w: 12 }];   // a caption line, a y-axis label
  assert.deepEqual(figureRegion(boxes, caption, 612, 792, texts), [40, 488, 304, 760]);
  assert.equal(figureRegion([[330, 520, 560, 700]], caption, 612, 792), null);                 // nothing in this column
  assert.equal(figureRegion([[0, 0, 612, 792]], caption, 612, 792), null);                       // a page background
  // a table: the drawing (rules) below its caption
  const tcap = { top: 300, bottom: 288, x: 330, table: true };
  const rules = [[330, 280, 560, 281], [330, 262, 560, 263], [330, 150, 560, 151]];               // top, mid, bottom rule
  const rows = [270, 250, 238, 226, 214, 202, 190, 178, 166, 154].map((y) => ({ x: 340, y, w: 200 }));
  const body = [130, 118, 106, 94].map((y) => ({ str: 'Running text after the table, a full line of it.', x: 330, y, w: 230 }));
  rules.push([330, 80, 560, 81]);                                                                  // another table's rule further down
  assert.deepEqual(figureRegion(rules, tcap, 612, 792, [...rows.map((r) => ({ ...r, str: '1 2 3' })), ...body]), [330, 150, 560, 300]);
  assert.equal(figureRegion([[330, 150, 560, 151]], tcap, 612, 792, body), null);                // no rule under the caption
});

test('figureRegion: gaps inside a figure, labels above it, and where it must stop', () => {
  const caption = { top: 400, bottom: 380, x: 54, table: false };
  const prose = (y) => ({ str: 'This is a full line of running body text in the column.', x: 54, y, w: 240 });
  // two panels 35pt apart (bigger than a line), a panel label "(a)" and a title above the top panel
  const boxes = [[60, 410, 290, 520], [60, 555, 290, 650]];
  const texts = [{ str: '(a)', x: 60, y: 652, w: 10, h: 8 }, { str: 'Energy vs time', x: 120, y: 660, w: 70, h: 8 }];
  assert.deepEqual(figureRegion(boxes, caption, 612, 792, texts).slice(1, 4), [380, 290, 668]);
  // the body text above the figure, and a figure above that text, are not part of it
  const above = [...boxes, [60, 700, 290, 760]];
  const r = figureRegion(above, caption, 612, 792, [...texts, prose(680), prose(668 + 10)]);
  assert.ok(r[3] < 690, String(r));
  // the short last line of the paragraph just above the figure is not a label
  const tail = { str: 'with Fig. 9b.', x: 54, y: 662, w: 60, h: 8 };
  assert.ok(figureRegion(boxes, caption, 612, 792, [...texts, tail, prose(674)])[3] < 669);
  // a background rectangle behind the whole column (with body text in it) is ignored
  const bg = [...boxes, [50, 100, 300, 780]];
  assert.ok(figureRegion(bg, caption, 612, 792, [...texts, prose(300), prose(720)])[3] < 690);

});

test('fitRegion: the whole region in the popup, scale within limits', () => {
  const tall = fitRegion({ x: 0, y: 0, w: 150, h: 460 }, 640, 620);
  assert.ok(tall.s * 460 + 28 <= 620 + 1 && tall.h <= 620, JSON.stringify(tall));
  const small = fitRegion({ x: 0, y: 0, w: 100, h: 50 }, 640, 620);
  assert.equal(small.s, 1.6);                   // not blown up beyond maxS
  assert.ok(small.w >= 320);
  assert.equal(fitRegion({ x: 0, y: 0, w: 5000, h: 5000 }, 640, 620).s, 0.45);
});
