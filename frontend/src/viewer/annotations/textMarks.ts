// Highlighting and underlining selected text: with a mouse, the Pencil or a finger, snapped to
// whole words, one rect per line. Also the words under an area highlight. See annotations.ts for
// the overview.
import { mergeLines, wordEnd, wordStart } from './lines.js';
import { type Annotation, attId, color, create, el, layers, pageSize, scale, size, tool } from './model.js';
import { lastPointer } from './pointer.js';
import { prefs } from './prefs.js';

const r6 = (v: number) => Math.round(v * 1e6) / 1e6;
const isMarkTool = (t: string): t is 'highlight' | 'underline' => t === 'highlight' || t === 'underline';

/** The range widened to whole words, without the spaces at its ends. Only text inside a run of
    the text layer is looked at, so a word split over two runs keeps the part outside. */
export function snapToWords(range: Range): Range {
  const r = range.cloneRange();
  const { startContainer: s, endContainer: e } = r;
  const same = s === e;
  if (s.nodeType === Node.TEXT_NODE) r.setStart(s, wordStart(s.textContent || '', r.startOffset, same ? r.endOffset : undefined));
  if (e.nodeType === Node.TEXT_NODE) r.setEnd(e, wordEnd(e.textContent || '', r.endOffset, same ? r.startOffset : 0));
  return r.collapsed || !r.toString().trim() ? range : r;
}

/** The page a range starts on and its lines, as [x, y, w, h] page fractions; or null. */
function rangeRects(range: Range): { wrapper: HTMLElement; rects: number[][] } | null {
  if (range.collapsed) return null;
  const start = range.startContainer;
  const wrapper = (start.nodeType === 1 ? (start as Element) : start.parentElement)?.closest<HTMLElement>('.page-wrapper');
  if (!wrapper) return null;
  const wr = wrapper.getBoundingClientRect();
  const raw = [...range.getClientRects()].filter((a) => {   // on this page only
    const cx = (a.left + a.right) / 2, cy = (a.top + a.bottom) / 2;
    return cx >= wr.left && cx <= wr.right && cy >= wr.top && cy <= wr.bottom;
  });
  // keep the innermost text boxes (drop paragraph-sized wrappers and duplicates)
  const boxes = raw.filter((a, i) => a.width >= 1 && a.height >= 1 && !raw.some((b, j) => {
    if (i === j) return false;
    const same = Math.abs(a.left - b.left) < 1 && Math.abs(a.right - b.right) < 1 && Math.abs(a.top - b.top) < 1 && Math.abs(a.bottom - b.bottom) < 1;
    if (same) return i > j;
    return a.left <= b.left + 1 && a.right >= b.right - 1 && a.top <= b.top + 1 && a.bottom >= b.bottom - 1;
  }));
  if (!boxes.length) return null;
  const [W, H] = pageSize(wrapper);
  const s = scale();
  return {
    wrapper,
    rects: mergeLines(boxes).map((b) => [r6((b.left - wr.left) / s / W), r6((b.top - wr.top) / s / H),
      r6((b.right - b.left) / s / W), r6((b.bottom - b.top) / s / H)]),
  };
}

/** The selection's range as it will be marked: snapped to words unless `exact`. */
function markedRange(sel: Selection | null, exact = false): Range | null {
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  return prefs.snapWords && !exact ? snapToWords(range) : range;
}

/** The selected text becomes one annotation of type t, one rect per line. Returns true when made. */
function markSelection(t: string, exact = false): boolean {
  if (!isMarkTool(t)) return false;   // the tool changed meanwhile
  const sel = window.getSelection();
  const range = attId ? markedRange(sel, exact) : null;
  const got = range && rangeRects(range);
  if (!got || !sel) return false;
  const text = range.toString().replace(/-\n(?=\w)/g, '').replace(/\s+/g, ' ').trim();
  const rec: Omit<Annotation, 'id'> = { type: t, page: Number(got.wrapper.dataset.pageNumber), rects: got.rects, text, color: color() };
  if (t === 'underline') rec.size = size();
  sel.removeAllRanges();
  hideMarkPill();
  hidePreview();
  create(rec);
  return true;
}

document.addEventListener('mouseup', (e) => {
  if (isMarkTool(tool()) && lastPointer === 'mouse') markSelection(tool(), e.altKey);
});

/** The words of the text layer inside [x, y, w, h] (page fractions) of a page. */
export function textIn(wrapper: HTMLElement, [x, y, w, h]: number[]): string {
  const wr = wrapper.getBoundingClientRect();
  const words: string[] = [];
  for (const span of wrapper.querySelectorAll<HTMLElement>('.textLayer span')) {
    const r = span.getBoundingClientRect();
    if (!r.width || span.children.length) continue;
    const cx = ((r.left + r.right) / 2 - wr.left) / wr.width, cy = ((r.top + r.bottom) / 2 - wr.top) / wr.height;
    if (cx >= x && cx <= x + w && cy >= y && cy <= y + h) words.push(span.textContent || '');
  }
  return words.join(' ').replace(/-\s+(?=\w)/g, '').replace(/\s+/g, ' ').trim();
}

/** Where in the text a point is (a collapsed range), or null. */
function caretAt(x: number, y: number): Range | null {
  if (document.caretRangeFromPoint) return document.caretRangeFromPoint(x, y);
  const pos = document.caretPositionFromPoint?.(x, y);
  if (!pos) return null;
  const r = document.createRange();
  r.setStart(pos.offsetNode, pos.offset);
  return r;
}
const inText = (r: Range | null): r is Range => !!r && !!(r.startContainer.nodeType === 1 ? (r.startContainer as Element) : r.startContainer.parentElement)?.closest?.('.textLayer');

// The Pencil drags over the text: the selection follows it, and lifting the pen marks it.
let penSel: { id: number; start: Range } | null = null;
document.addEventListener('pointerdown', (e) => {
  if (!isMarkTool(tool()) || e.pointerType !== 'pen' || !(e.target as Element).closest?.('.page-wrapper')) return;
  const start = caretAt(e.clientX, e.clientY);
  if (!inText(start)) return;
  e.preventDefault();
  penSel = { id: e.pointerId, start };
  window.getSelection()?.removeAllRanges();
});
document.addEventListener('pointermove', (e) => {
  if (!penSel || e.pointerId !== penSel.id) return;
  const here = caretAt(e.clientX, e.clientY);
  if (!inText(here)) return;              // between lines: keep the last good selection
  const r = document.createRange();
  const forward = penSel.start.compareBoundaryPoints(Range.START_TO_START, here) <= 0;
  const [a, b] = forward ? [penSel.start, here] : [here, penSel.start];
  r.setStart(a.startContainer, a.startOffset);
  r.setEnd(b.startContainer, b.startOffset);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(r);
});
const endPenSel = (e: PointerEvent) => {
  if (!penSel || e.pointerId !== penSel.id) return;
  penSel = null;
  if (e.type === 'pointerup') markSelection(tool(), e.altKey);
};
document.addEventListener('pointerup', endPenSel);
document.addEventListener('pointercancel', endPenSel);

/* While a finger (or the Pencil) is still selecting, the would-be mark is drawn in the tool's
   colour, snapped as it will be: iPadOS may not show the selection itself until the finger is
   lifted. */
function hidePreview(): void { document.querySelectorAll('.zc-sel-preview').forEach((n) => n.remove()); }
document.addEventListener('selectionchange', () => {
  hidePreview();
  const t = tool();
  if (!isMarkTool(t) || lastPointer === 'mouse') return;
  const range = markedRange(window.getSelection());
  const got = range && rangeRects(range);
  const svg = got && layers.get(Number(got.wrapper.dataset.pageNumber));
  if (!svg) return;
  const [W, H] = pageSize(got.wrapper);
  const g = el('g', { class: 'zc-sel-preview', 'pointer-events': 'none' });
  for (const [x, y, w, h] of got.rects) {
    g.appendChild(t === 'highlight'
      ? el('rect', { x: x * W, y: y * H, width: w * W, height: h * H, fill: color(), 'fill-opacity': 0.5 })
      : el('line', { x1: x * W, y1: (y + h) * H, x2: (x + w) * W, y2: (y + h) * H, stroke: color(), 'stroke-width': size(), 'stroke-opacity': 0.7 }));
  }
  svg.appendChild(g);
});

// A finger selects with a long press (and the handles); a button then makes the mark.
let markPill: (HTMLDivElement & { zcRange?: Range }) | null = null;
function hideMarkPill(): void { markPill?.remove(); markPill = null; }
let selTimer: ReturnType<typeof setTimeout> | undefined;
document.addEventListener('selectionchange', () => {
  clearTimeout(selTimer);
  selTimer = setTimeout(() => {
    const t = tool();
    if (!isMarkTool(t) || lastPointer !== 'touch' || penSel) { hideMarkPill(); return; }
    const sel = window.getSelection();
    const node = sel?.anchorNode?.nodeType === 1 ? (sel.anchorNode as Element) : sel?.anchorNode?.parentElement;
    if (!sel || sel.isCollapsed || !node?.closest?.('.textLayer')) { hideMarkPill(); return; }
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    if (!markPill) {
      markPill = document.createElement('div');
      markPill.className = 'zc-pill zc-mark-pill';
      markPill.addEventListener('pointerdown', (ev) => ev.preventDefault());
      markPill.addEventListener('click', () => {
        // a tap elsewhere may have cleared the selection by now: put back the one the button was made for
        const sel2 = window.getSelection()!;
        if (markPill?.zcRange) { sel2.removeAllRanges(); sel2.addRange(markPill.zcRange); }
        markSelection(tool());
      });
      document.body.appendChild(markPill);
    }
    markPill.zcRange = sel.getRangeAt(0).cloneRange();
    markPill.innerHTML = `<button data-mark>${t === 'underline' ? 'Underline' : 'Highlight'}</button>`;
    markPill.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - 140))}px`;
    markPill.style.top = `${rect.top - 52 < 50 ? rect.bottom + 52 : rect.top - 52}px`;
  }, 250);
});
document.getElementById('pdf-container')?.addEventListener('scroll', hideMarkPill, { passive: true });
document.addEventListener('pointerdown', (e) => { if (markPill && !markPill.contains(e.target as Node)) hideMarkPill(); });
