// Finding a quote on a PDF page (issue #8): the words of a "Quote to note" are looked up in the
// page's text, so a citation opens the viewer right where the quote is and flashes it.
//
// Matching ignores case, accents, spacing, punctuation and line-break hyphens, so the text as
// cleaned up in the note ("trans- port" → "transport") still matches the page. Pure functions:
// unit-tested with Node (tests/js/citeTarget.test.mjs).

const HEAD = 40;   // characters (normalised) used to find a quote that is not found whole
const LETTERS = { æ: 'ae', œ: 'oe', ß: 'ss', ø: 'o', ł: 'l', đ: 'd', ı: 'i' };   // no Unicode decomposition

/** Lower-case letters and digits only, plus where each one came from in the original. */
export function normalise(str) {
  let out = '';
  const at = [];
  for (let i = 0; i < str.length; i++) {
    const lower = str[i].normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    const plain = LETTERS[lower] || lower;
    for (const ch of plain) {
      if (/[\p{L}\p{N}]/u.test(ch)) { out += ch; at.push(i); }
    }
  }
  return { text: out, at };
}

/**
 * Where `quote` is among the page's text `items` ([{str, x, y, w, h}] — x, y the top-left
 * corner, in page units). Returns the rectangles covering it, one per line, or [] if not found.
 */
export function findQuote(items, quote) {
  const q = normalise(quote).text;
  if (q.length < 4) return [];
  // the page as one normalised string, remembering item and character of each letter
  let text = '';
  const from = [];
  items.forEach((it, n) => {
    const { text: t, at } = normalise(it.str || '');
    text += t;
    for (const i of at) from.push([n, i]);
  });
  let start = text.indexOf(q), end = start + q.length;
  if (start < 0) {
    // not whole: it may go on to the next page, or differ somewhere (a footnote mark, a ligature…)
    const head = q.slice(0, HEAD), tail = q.slice(-HEAD);
    const h = q.length >= HEAD ? text.indexOf(head) : -1;
    const t = q.length >= HEAD ? text.indexOf(tail, h < 0 ? 0 : h) : -1;
    if (h >= 0 && t >= 0 && t - h < q.length * 1.5) { start = h; end = t + tail.length; }
    else if (h >= 0) { start = h; end = Math.min(text.length, h + q.length); }
    else if (t >= 0) { start = Math.max(0, t + tail.length - q.length); end = t + tail.length; }
    else return [];
  }
  // character ranges per item → rectangles (a proportional share of each item's width)
  const spans = new Map();
  for (let k = start; k < end; k++) {
    const [n, i] = from[k];
    const s = spans.get(n);
    if (s) s[1] = i + 1; else spans.set(n, [i, i + 1]);
  }
  const rects = [];
  for (const [n, [a, b]] of [...spans].sort((x, y) => x[0] - y[0])) {
    const it = items[n];
    const len = Math.max(1, it.str.length);
    const r = { x: it.x + (it.w * a) / len, y: it.y, w: (it.w * (b - a)) / len, h: it.h };
    const last = rects[rects.length - 1];
    // same line (overlapping vertically, next to each other): one rectangle
    if (last && Math.abs(last.y - r.y) < Math.max(last.h, r.h) * 0.5 && r.x >= last.x - 2 && r.x - (last.x + last.w) < r.h * 3) {
      const right = Math.max(last.x + last.w, r.x + r.w);
      const top = Math.min(last.y, r.y), bottom = Math.max(last.y + last.h, r.y + r.h);
      Object.assign(last, { w: right - last.x, y: top, h: bottom - top });
    } else rects.push(r);
  }
  return rects;
}

/**
 * One box around the quote's lines, so it reads as a single highlight. A quote that goes on
 * into the next column gets one box per column (a single box would cover the text between).
 */
export function quoteBoxes(rects) {
  const boxes = [];
  for (const r of rects) {
    // same column: overlaps horizontally with a box and starts below its top
    const b = boxes.find((x) => r.x < x.x + x.w && r.x + r.w > x.x && r.y >= x.y - r.h);
    if (b) {
      const right = Math.max(b.x + b.w, r.x + r.w), bottom = Math.max(b.y + b.h, r.y + r.h);
      b.x = Math.min(b.x, r.x); b.y = Math.min(b.y, r.y);
      b.w = right - b.x; b.h = bottom - b.y;
    } else boxes.push({ ...r });
  }
  return boxes;
}

/** "x,y,w,h" (fractions of the page) → a rectangle in page units, or null. */
export function parseRect(s, pageW, pageH) {
  const v = String(s || '').split(',').map(Number);
  if (v.length !== 4 || v.some((n) => !Number.isFinite(n)) || v[2] <= 0 || v[3] <= 0) return null;
  const c = (n) => Math.min(1, Math.max(0, n));
  return { x: c(v[0]) * pageW, y: c(v[1]) * pageH, w: c(v[2]) * pageW, h: c(v[3]) * pageH };
}
