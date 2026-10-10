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
 * items [{str, x, y, h}] → {top, bottom, table} of the caption paragraph, or null. `pageW` is the
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
    if (!best || d < best.d) best = { d, top: it.y + lineH, bottom: last - lineH * 0.3, table: /^\s*tab/i.test(it.str) };
  }
  return best && { top: best.top, bottom: best.bottom, table: best.table };
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
