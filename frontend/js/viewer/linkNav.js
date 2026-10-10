// Pure helpers for links in the PDF viewer (no DOM, unit-tested in tests/js/linkNav.test.mjs).

/**
 * Where an explicit PDF destination points: [pageRef, {name}, ...args] → {ref, x, y}, with x/y in
 * PDF user space (y grows upwards) or null when the destination leaves them free.
 * See PDF 32000-1 §12.3.2.2 for the destination kinds.
 */
export function destTarget(dest) {
  if (!Array.isArray(dest) || dest.length < 2) return null;
  const [ref, kind, ...args] = dest;
  const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  switch (kind?.name) {
    case 'XYZ': return { ref, x: num(args[0]), y: num(args[1]) };
    case 'FitH': case 'FitBH': return { ref, x: null, y: num(args[0]) };
    case 'FitV': case 'FitBV': return { ref, x: num(args[0]), y: null };
    case 'FitR': return { ref, x: num(args[0]), y: num(args[3]) };
    default: return { ref, x: null, y: null };   // Fit, FitB, unknown: the top of the page
  }
}

/** A rectangle in page CSS pixels [x1, y1, x2, y2] (any corner order) → percentages of the page. */
export function rectPercent(r, width, height) {
  const left = Math.min(r[0], r[2]), right = Math.max(r[0], r[2]);
  const top = Math.min(r[1], r[3]), bottom = Math.max(r[1], r[3]);
  const pct = (v, of) => `${(100 * v / of).toFixed(4)}%`;
  return { left: pct(left, width), top: pct(top, height), width: pct(right - left, width), height: pct(bottom - top, height) };
}

/**
 * The reading position for a scroll offset: the page at the top of the view and how far into it,
 * as a fraction of its height — so it survives zooming. `pages` are [{num, top, height}] in
 * scroll coordinates, in order.
 */
export function positionAt(pages, scrollTop) {
  if (!pages.length) return null;
  let p = pages[0];
  for (const q of pages) {
    if (q.top <= scrollTop) p = q; else break;
  }
  const frac = p.height > 0 ? (scrollTop - p.top) / p.height : 0;
  return { page: p.num, frac: Math.round(Math.min(Math.max(frac, -0.2), 1.2) * 10000) / 10000 };
}

/** The scroll offset for a position (inverse of positionAt), or null if its page isn't laid out. */
export function scrollFor(pages, pos) {
  const p = pages.find((q) => q.num === pos?.page);
  return p ? p.top + (pos.frac || 0) * p.height : null;
}

/** Where a popup of size w×h goes next to an anchor rect, inside a viewport of vw×vh. */
export function popupPlacement(anchor, w, h, vw, vh, gap = 8) {
  const below = anchor.bottom + gap + h <= vh || anchor.top - gap - h < 0;
  const top = below ? Math.min(anchor.bottom + gap, Math.max(vh - h - 4, 4)) : anchor.top - gap - h;
  const left = Math.min(Math.max(anchor.left, 4), Math.max(vw - w - 4, 4));
  return { left: Math.round(left), top: Math.round(top), below };
}

/**
 * Top-left corner (page CSS px) of a preview window w×h onto a pageW×pageH page, for a
 * destination at (x, y) (either may be null).
 * Horizontally the window is centred on the destination's column (the left or right half of the
 * page, as in two-column papers, where section titles are centred in their column); vertically
 * the destination sits near the top, or — with `bottom` (page CSS px) — that line sits near the
 * bottom of the window (figures: the caption is at the bottom, the figure above it).
 */
export function previewOrigin(x, y, w, h, pageW, pageH, margin = 16, bottom = null) {
  const clampX = (v) => Math.max(0, Math.min(v, pageW - w));
  const clampY = (v) => Math.max(0, Math.min(v, pageH - h));
  let ox = 0;
  if (x != null && pageW > w) {
    const half = pageW / 2;
    const colCentre = x < half ? half / 2 : half + half / 2;
    ox = clampX(colCentre - w / 2);
    if (x < ox + margin || x > ox + w - margin) ox = clampX(x - margin);   // never hide the destination itself
  }
  let oy = 0;
  if (bottom != null) oy = clampY(bottom - h + margin);
  else if (y != null) oy = clampY(y - margin);
  return { x: Math.round(ox), y: Math.round(oy) };
}

// "FIG. 1.", "Figure 1:", "Figure 1 |", "TABLE II." (or the number alone in its run) — not prose
// like "Fig. 1, Fig. 2 and…" or "Fig. 3 shows…".
const CAPTION = /^\s*(fig(ure)?s?|tab(le)?|scheme|chart|algorithm|listing)\.?\s*(S?\d+|[IVXLC]+)\s*([.:|]|$)/i;

/**
 * The caption at a destination, from the page's text (PDF coordinates, y up):
 * items [{str, x, y, h}] → {top, bottom, x, table} of the caption paragraph, or null. `pageW` is the
 * page's width (the columns are its two halves). `table`: a table's caption, which sits above it.
 * A caption begins its line with "FIG. 3.", "Figure 3:", "TABLE II"… in the destination's column.
 * Link targets are placed about a line above the text they mark, so the destination is a caption
 * when it sits at the caption's first line (anchored at its start) or at its last line (anchored
 * at its end, as most LaTeX figures are).
 */
export function findCaption(items, destX, destY, pageW) {
  if (destY == null) return null;
  const half = pageW / 2;
  const col = (x) => (pageW > 0 ? x < half : true);
  const inCol = destX == null ? null : col(destX);
  const lineOf = (it) => items.filter((o) => Math.abs(o.y - it.y) < Math.max(1, (it.h || 10) * 0.3) && col(o.x) === col(it.x));
  let best = null;
  for (const it of items) {
    if (!CAPTION.test(it.str) || (inCol !== null && col(it.x) !== inCol)) continue;
    if (lineOf(it).some((o) => o.x < it.x - 1 && o.str.trim())) continue;   // "Fig. 3" inside a sentence
    const lineH = it.h || 10;
    const last = paragraphEnd(items, it, col);
    const atStart = Math.abs(it.y + lineH - destY) <= lineH * 1.6;
    const atEnd = Math.abs(last + lineH - destY) <= lineH * 1.6;
    if (!atStart && !atEnd) continue;
    const d = Math.min(Math.abs(it.y + lineH - destY), Math.abs(last + lineH - destY));
    if (!best || d < best.d) best = { d, top: it.y + lineH, bottom: last - lineH * 0.3, x: it.x, table: /^\s*tab/i.test(it.str) };
  }
  return best && { top: best.top, bottom: best.bottom, x: best.x, table: best.table };
}

/** Baseline of the last line of the paragraph starting at `start` (lines in its column, line-break gaps). */
function paragraphEnd(items, start, col) {
  const lineH = start.h || 10;
  const below = items.filter((it) => it.str.trim() && col(it.x) === col(start.x) && it.y <= start.y + 0.5)
    .sort((a, b) => b.y - a.y);
  let last = start.y;
  for (const it of below) {
    if (last - it.y > lineH * 1.9) break;
    last = Math.min(last, it.y);
  }
  return last;
}

/**
 * A named destination of a float (LaTeX hyperref: figure.3, table.2, subfigure.1.2, figure.caption.7,
 * algocf.1, lstlisting.4…): its anchor is at the caption, usually below the figure itself.
 */
export function isFloatDest(dest) {
  return typeof dest === 'string' && /^(sub)?(figure|table)\b|^(algocf|algorithm|lstlisting|listing)\b/i.test(dest);
}

/* ------------------------------------------------------------------ figures */
const mul = (m, n) => [m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1], m[0] * n[2] + m[2] * n[3],
  m[1] * n[2] + m[3] * n[3], m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5]];
const apply = (m, x, y) => [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
function boxOf(m, x0, y0, x1, y1) {
  const pts = [apply(m, x0, y0), apply(m, x1, y0), apply(m, x0, y1), apply(m, x1, y1)];
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

/**
 * Where a page draws things: boxes [x0, y0, x1, y1] (PDF coordinates, y up) of its images and of
 * the paths it paints (fills and strokes, not clips), from pdf.js's operator list
 * (page.getOperatorList(): fnArray, argsArray) and its OPS table. Text is not included.
 */
export function graphicsBoxes(fnArray, argsArray, OPS) {
  const PAINT = new Set([OPS.stroke, OPS.closeStroke, OPS.fill, OPS.eoFill, OPS.fillStroke, OPS.eoFillStroke,
    OPS.closeFillStroke, OPS.closeEOFillStroke, OPS.shadingFill]);
  const IMAGE = new Set([OPS.paintImageXObject, OPS.paintInlineImageXObject, OPS.paintImageMaskXObject,
    OPS.paintImageXObjectRepeat, OPS.paintImageMaskXObjectRepeat, OPS.paintSolidColorImageMask, OPS.paintInlineImageXObjectGroup,
    OPS.paintImageMaskXObjectGroup]);
  let m = [1, 0, 0, 1, 0, 0];
  const stack = [];
  const out = [];
  let pending = null;   // the box of the last constructed path, until we know whether it is painted
  for (let i = 0; i < fnArray.length; i++) {
    const fn = fnArray[i], args = argsArray[i];
    if (fn === OPS.save) stack.push(m);
    else if (fn === OPS.restore) m = stack.pop() || m;
    else if (fn === OPS.transform) m = mul(m, args);
    else if (fn === OPS.paintFormXObjectBegin) { stack.push(m); if (Array.isArray(args?.[0]) && args[0].length === 6) m = mul(m, args[0]); }
    else if (fn === OPS.paintFormXObjectEnd) m = stack.pop() || m;
    else if (fn === OPS.constructPath) {
      const mm = args?.[2];
      pending = Array.isArray(mm) || ArrayBuffer.isView(mm) ? boxOf(m, mm[0], mm[2], mm[1], mm[3]) : null;
      continue;
    } else if (PAINT.has(fn)) { if (pending) out.push(pending); }
    else if (IMAGE.has(fn)) out.push(boxOf(m, 0, 0, 1, 1));
    pending = null;
  }
  return out.filter((b) => Number.isFinite(b[0]) && b[2] - b[0] + (b[3] - b[1]) > 0.5);
}

/**
 * The figure that goes with a caption: the drawing just above it (below it for a table), in the
 * caption's column — grown box by box while the next one is close — as [x0, y0, x1, y1] including
 * the caption and the text inside the figure (labels), or null if nothing is drawn there.
 * `caption` comes from findCaption; `texts` are the page's text items [{x, y, w}].
 */
export function figureRegion(boxes, caption, pageW, pageH, texts = []) {
  if (!caption) return null;
  const half = pageW / 2;
  const left = caption.x < half;
  // in the caption's column: mostly on its side of the middle, or a full-width figure
  const inCol = (b) => b[2] - b[0] > half * 1.1 || (left ? (b[0] + b[2]) / 2 < half : (b[0] + b[2]) / 2 >= half);
  const big = (b) => b[2] - b[0] > pageW * 0.9 && b[3] - b[1] > pageH * 0.9;   // a page background
  const cand = boxes.filter((b) => inCol(b) && !big(b));
  const GAP = 24;
  let lo, hi;
  if (!caption.table) {
    // start with what ends just above the caption, then keep adding what touches the region from above
    let top = null;
    const near = cand.filter((b) => b[1] >= caption.top - 6 - GAP / 2 && b[1] <= caption.top + GAP && b[3] > caption.top - 2);
    if (!near.length) return null;
    top = Math.max(...near.map((b) => b[3]));
    let grew = true;
    while (grew) {
      grew = false;
      for (const b of cand) {
        if (b[3] > top && b[1] <= top + GAP && b[1] >= caption.top - 6) { top = b[3]; grew = true; }
      }
    }
    lo = caption.bottom; hi = top;
  } else {
    // a table: its rules below the caption, connected through its rows of text; it ends at the
    // last rule reached (the body text after it is not part of it)
    const near = cand.filter((b) => b[3] <= caption.bottom + 6 + GAP / 2 && b[3] >= caption.bottom - GAP && b[1] < caption.bottom + 2);
    if (!near.length) return null;
    const rows = texts.filter((it) => Number.isFinite(it.w) && (it.w > half * 1.1 || ((it.x + it.w / 2) < half) === left))
      .map((it) => [it.x, it.y, it.x + it.w, it.y + 8]);
    let reach = Math.min(...near.map((b) => b[1]));
    let bottom = reach;
    let grew = true;
    while (grew) {
      grew = false;
      for (const b of cand) if (b[1] < reach && b[3] >= reach - GAP && b[3] <= caption.bottom + 6) { reach = b[1]; bottom = Math.min(bottom, b[1]); grew = true; }
      for (const b of rows) if (b[1] < reach && b[3] >= reach - GAP && b[1] < caption.bottom - 6) { reach = b[1]; grew = true; }
    }
    lo = bottom; hi = caption.top;
  }
  const inside = cand.filter((b) => b[1] >= lo - 1 && b[3] <= hi + 1);
  let x0 = Math.min(caption.x, ...inside.map((b) => b[0]));
  let x1 = Math.max(caption.x + 40, ...inside.map((b) => b[2]));
  // the caption's lines and the figure's labels, in the same column
  for (const it of texts) {
    if (it.y < lo - 1 || it.y > hi + 1 || !Number.isFinite(it.w)) continue;
    const mid = it.x + it.w / 2;
    if (it.w < half * 1.1 && (mid < half) !== left) continue;
    x0 = Math.min(x0, it.x); x1 = Math.max(x1, it.x + it.w);
  }
  return [x0, lo, x1, hi];
}

/**
 * How to show a region (page CSS px at scale 1: {x, y, w, h}) in a popup of at most maxW×maxH:
 * the scale (between minS and maxS) and the popup's size, with `pad` around the region.
 */
export function fitRegion(r, maxW, maxH, { pad = 14, minS = 0.45, maxS = 1.6 } = {}) {
  const s = Math.max(minS, Math.min(maxS, (maxW - 2 * pad) / r.w, (maxH - 2 * pad) / r.h));
  const w = Math.round(Math.min(maxW, Math.max(320, r.w * s + 2 * pad)));
  const h = Math.round(Math.min(maxH, Math.max(140, r.h * s + 2 * pad)));
  return { s, w, h };
}
