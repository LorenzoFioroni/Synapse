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
 * Top-left corner (page CSS px) of a preview window w×h showing (x, y) of a pageW×pageH page:
 * the point near the top-left (align 'top'), or near the bottom ('bottom', for figures whose
 * destination is the end of the caption, below the figure).
 */
export function previewOrigin(x, y, w, h, pageW, pageH, margin = 16, align = 'top') {
  const ox = x == null ? 0 : Math.max(0, Math.min(x - margin, pageW - w));
  const want = align === 'bottom' ? y - h + 2 * margin : y - margin;
  const oy = y == null ? 0 : Math.max(0, Math.min(want, pageH - h));
  return { x: Math.round(ox), y: Math.round(oy) };
}

/**
 * A named destination of a float (LaTeX hyperref: figure.3, table.2, subfigure.1.2, figure.caption.7,
 * algocf.1, lstlisting.4…): its anchor is at the caption, usually below the figure itself.
 */
export function isFloatDest(dest) {
  return typeof dest === 'string' && /^(sub)?(figure|table)\b|^(algocf|algorithm|lstlisting|listing)\b/i.test(dest);
}
