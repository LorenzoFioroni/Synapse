// Pointer, Pencil and touch input for the annotation tools: drawing boxes and ink, text boxes,
// the eraser, highlighting and underlining selected text, and the Delete button and keyboard
// shortcuts for a selected annotation. See annotations.ts for the overview.
import {
  type Annotation, type TextBoxNode, attId, byId, change, color, create, el, hooks, layers, measure, pageSize, placeText,
  redoStack, remove, scale, select, selectedId, size, textBox, tool, uid, undo, undoStack,
} from './model.js';

/** A point on a page in PDF points, with the page's size. */
interface Point { x: number; y: number; W: number; H: number }
const r6 = (v: number) => Math.round(v * 1e6) / 1e6;

/* ------------------------------------------------------------------ input */
/* Mouse, Apple Pencil and fingers all arrive as pointer events.
   - Once a pen has been used (remembered), fingers scroll and pinch while the pen draws, highlights
     and erases; before that (a phone, an iPad without a Pencil) fingers draw too.
   - Safari scrolls the page under the Pencil unless its touches are cancelled; touchstart/touchmove
     of a stylus are cancelled while an annotation tool is chosen (see stopPenScroll).
   - Highlight / underline: a mouse selects text as usual; the Pencil drags over the text (the
     selection follows it); a finger selects with a long press and then taps "Highlight". */
let penSeen = false;
try { penSeen = localStorage.getItem('zc.penSeen') === '1'; } catch { /* private mode */ }
document.body.classList.toggle('zc-pen', penSeen);
function notePen(e: PointerEvent): void {
  if (e.pointerType !== 'pen' || penSeen) return;
  penSeen = true;
  document.body.classList.add('zc-pen');
  try { localStorage.setItem('zc.penSeen', '1'); } catch { /* private mode */ }
}
/** May this pointer draw? Mouse and pen always; a finger only while no pen has been seen. */
const draws = (e: PointerEvent) => e.pointerType !== 'touch' || !penSeen;
let lastPointer = 'mouse';
document.addEventListener('pointerdown', (e) => { lastPointer = e.pointerType || 'mouse'; notePen(e); }, true);

function stopPenScroll(e: TouchEvent): void {
  if (tool() === 'select') return;
  // touchType is Safari's (not in the standard DOM types)
  if (![...e.changedTouches].some((t) => (t as Touch & { touchType?: string }).touchType === 'stylus')) return;
  if (!(e.target as Element).closest?.('.page-wrapper')) return;
  // a tap with the text tool must stay a tap (it opens the keyboard); everything else is drawn
  if (tool() === 'text' && e.type === 'touchstart') return;
  e.preventDefault();
}
document.addEventListener('touchstart', stopPenScroll, { passive: false });
document.addEventListener('touchmove', stopPenScroll, { passive: false });

function pointIn(svg: SVGSVGElement, e: { clientX: number; clientY: number }): Point {
  const r = svg.getBoundingClientRect();
  const [W, H] = pageSize(svg.parentElement!);
  return { x: (e.clientX - r.left) / scale(), y: (e.clientY - r.top) / scale(), W, H };
}

function newTextBox(html: HTMLElement, page: number, p: Point): void {
  const draft: Omit<Annotation, 'id'> = { type: 'text', page, rects: [[p.x / p.W, p.y / p.H, 0, 0]], text: '', color: color(), size: size() };
  const box = textBox({ ...draft, id: `draft-${uid()}` }, p.x, p.y, p.W, p.H);
  box.classList.add('anno-draft');
  box.zcDraft = draft;
  html.appendChild(box);
  // focus right away, inside the tap: iPadOS only opens the keyboard (or Scribble) for a focus
  // that happens during the user's gesture
  box.querySelector<HTMLElement>('.anno-text-input')!.focus();
}

/** Pointer input on a page: the SVG layer and the text boxes' layer above it share these handlers
    (positions are always taken from the SVG layer, which covers the page as the other one does). */
export function setupLayer(svg: SVGSVGElement, html: HTMLElement, page: number): void {
  // box or ink being drawn
  let shape: { kind: 'box' | 'draw'; g: SVGGElement; node: SVGPathElement | SVGRectElement; start: Point;
    points: number[][]; W: number; H: number; id: number } | null = null;
  // text box being moved
  let drag: { rec: Annotation; node: TextBoxNode; dx: number; dy: number; moved: boolean; id: number } | null = null;
  // text tool: where the pointer went down (a box is made when it comes up)
  let tap: { x: number; y: number; p: Point; id: number } | null = null;

  const down = (e: PointerEvent) => {
    const target = e.target as HTMLElement;
    const g = target.closest?.<HTMLElement>('.anno');
    const rec = g && byId.get(g.dataset.id!);
    const t = tool();
    if (t === 'eraser') {
      if (rec) { e.stopPropagation(); remove(rec); }
      return;
    }
    if (t === 'select') select(rec ? rec.id : null);
    // a text box: move it by its frame (typing goes to the text itself)
    const textNode = target.closest?.<TextBoxNode>('.anno-text');
    if (textNode) {
      e.stopPropagation();
      if (!target.isContentEditable && rec && draws(e)) {
        e.preventDefault();
        (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
        const p = pointIn(svg, e);
        drag = { rec, node: textNode, dx: p.x - textNode.zcX, dy: p.y - textNode.zcY, moved: false, id: e.pointerId };
      }
      return;
    }
    if (!['box', 'draw', 'text'].includes(t)) return;
    if (e.button > 0) return;
    const p = pointIn(svg, e);
    if (t === 'text') {
      e.preventDefault();
      tap = { x: e.clientX, y: e.clientY, p, id: e.pointerId };
      return;
    }
    if (!draws(e)) return;          // a finger while the Pencil draws: it scrolls
    e.preventDefault();
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    const g2 = el('g', { class: 'anno anno-draft' });
    const node = t === 'draw'
      ? el('path', { d: `M ${p.x} ${p.y}`, stroke: color(), 'stroke-width': size(), fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })
      : el('rect', { x: p.x, y: p.y, width: 0, height: 0, stroke: color(), 'stroke-width': size(), fill: 'none' });
    g2.appendChild(node);
    svg.appendChild(g2);
    shape = { kind: t as 'box' | 'draw', g: g2, node, start: p, points: [[p.x / p.W, p.y / p.H]], W: p.W, H: p.H, id: e.pointerId };
  };

  const move = (e: PointerEvent) => {
    if (drag && e.pointerId === drag.id) {
      const p = pointIn(svg, e);
      placeText(drag.node, p.x - drag.dx, p.y - drag.dy, p.W, p.H);
      drag.moved = true;
      return;
    }
    if (!shape || e.pointerId !== shape.id) return;
    // a Pencil reports more points than frames: use them all for a smooth line
    const events = shape.kind === 'draw' && e.getCoalescedEvents ? e.getCoalescedEvents() : [];
    for (const ev of events.length ? events : [e]) {
      const p = pointIn(svg, ev);
      if (shape.kind === 'draw') {
        shape.points.push([p.x / p.W, p.y / p.H]);
        shape.node.setAttribute('d', `${shape.node.getAttribute('d')} L ${p.x} ${p.y}`);
      } else {
        const s = shape.start;
        shape.node.setAttribute('x', String(Math.min(s.x, p.x)));
        shape.node.setAttribute('y', String(Math.min(s.y, p.y)));
        shape.node.setAttribute('width', String(Math.abs(p.x - s.x)));
        shape.node.setAttribute('height', String(Math.abs(p.y - s.y)));
      }
    }
  };

  const finish = (e: PointerEvent) => {
    if (tap && e.pointerId === tap.id) {
      const t0 = tap;
      tap = null;
      if (e.type === 'pointerup' && Math.hypot(e.clientX - t0.x, e.clientY - t0.y) < 10 && tool() === 'text') newTextBox(html, page, t0.p);
    }
    if (drag && e.pointerId === drag.id) {
      const { rec, node, moved } = drag;
      drag = null;
      if (moved) change(rec, { rects: [measure(node)] });
    }
    if (!shape || e.pointerId !== shape.id) return;
    const s = shape;
    shape = null;
    s.g.remove();
    if (e.type === 'pointercancel') return;   // the browser took the gesture over (a scroll)
    if (s.kind === 'draw') {
      if (s.points.length < 2) return;
      create({ type: 'ink', page, path: s.points.map(([x, y]) => [r6(x), r6(y)]), color: s.node.getAttribute('stroke')!, size: Number(s.node.getAttribute('stroke-width')) });
    } else {
      const [x, y, w, h] = ['x', 'y', 'width', 'height'].map((k) => parseFloat(s.node.getAttribute(k)!));
      if (w < 2 && h < 2) return;   // a click, not a box
      create({ type: 'box', page, rects: [[r6(x / s.W), r6(y / s.H), r6(w / s.W), r6(h / s.H)]], color: s.node.getAttribute('stroke')!, size: Number(s.node.getAttribute('stroke-width')) });
    }
  };
  for (const layer of [svg, html] as GlobalEventHandlers[]) {
    layer.addEventListener('pointerdown', down);
    layer.addEventListener('pointermove', move);
    layer.addEventListener('pointerup', finish);
    layer.addEventListener('pointercancel', finish);
  }
}

/* -------- eraser: drag over annotations to remove them (mouse, Pencil; a finger taps) */
let erasing: number | null = null;
document.addEventListener('pointerdown', (e) => {
  if (tool() !== 'eraser' || !draws(e) || e.button > 0 || !(e.target as Element).closest?.('.page-wrapper')) return;
  erasing = e.pointerId;
  eraseAt(e);
});
document.addEventListener('pointermove', (e) => { if (erasing === e.pointerId) eraseAt(e); });
document.addEventListener('pointerup', (e) => { if (erasing === e.pointerId) erasing = null; });
document.addEventListener('pointercancel', (e) => { if (erasing === e.pointerId) erasing = null; });
function eraseAt(e: PointerEvent): void {
  for (const n of document.elementsFromPoint(e.clientX, e.clientY)) {
    const g = n.closest?.<HTMLElement | SVGGElement>('.anno-layer .anno:not(.anno-draft)');
    const rec = g && byId.get(g.dataset.id!);
    if (rec) { remove(rec); return; }
  }
}

/* -------- highlight / underline */
/** The page of the selected text and its lines, as [x, y, w, h] page fractions; or null. */
function selectionRects(sel: Selection | null): { wrapper: HTMLElement; rects: number[][] } | null {
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const anchor = sel.anchorNode?.nodeType === 1 ? (sel.anchorNode as Element) : sel.anchorNode?.parentElement;
  const wrapper = anchor?.closest<HTMLElement>('.page-wrapper');
  if (!wrapper) return null;
  const raw = [...sel.getRangeAt(0).getClientRects()];
  // keep the innermost text boxes (drop paragraph-sized wrappers and duplicates)
  const rects = raw.filter((a, i) => a.width >= 1 && a.height >= 1 && !raw.some((b, j) => {
    if (i === j) return false;
    const same = Math.abs(a.left - b.left) < 1 && Math.abs(a.right - b.right) < 1 && Math.abs(a.top - b.top) < 1 && Math.abs(a.bottom - b.bottom) < 1;
    if (same) return i > j;
    return a.left <= b.left + 1 && a.right >= b.right - 1 && a.top <= b.top + 1 && a.bottom >= b.bottom - 1;
  }));
  if (!rects.length) return null;
  const wr = wrapper.getBoundingClientRect();
  const [W, H] = pageSize(wrapper);
  const s = scale();
  return { wrapper, rects: rects.map((r) => [r6((r.left - wr.left) / s / W), r6((r.top - wr.top) / s / H), r6(r.width / s / W), r6(r.height / s / H)]) };
}

/** The selected text becomes one annotation of type t, one rect per line. Returns true when made. */
function highlightSelection(t: string): boolean {
  if (t !== 'highlight' && t !== 'underline') return false;   // the tool changed meanwhile
  const sel = window.getSelection();
  const got = attId && selectionRects(sel);
  if (!got || !sel) return false;
  const { wrapper, rects: fr } = got;
  const text = sel.toString().replace(/-\n(?=\w)/g, '').replace(/\s+/g, ' ').trim();
  const rec: Omit<Annotation, 'id'> = { type: t, page: Number(wrapper.dataset.pageNumber), rects: fr, text, color: color() };
  if (t === 'underline') rec.size = size();
  sel.removeAllRanges();
  hideMarkPill();
  hidePreview();
  create(rec);
  return true;
}

document.addEventListener('mouseup', () => {
  const t = tool();
  if ((t === 'highlight' || t === 'underline') && lastPointer === 'mouse') highlightSelection(t);
});

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

// The Pencil drags over the text: the selection follows it, and lifting the pen highlights.
let penSel: { id: number; start: Range } | null = null;
document.addEventListener('pointerdown', (e) => {
  const t = tool();
  if ((t !== 'highlight' && t !== 'underline') || e.pointerType !== 'pen' || !(e.target as Element).closest?.('.page-wrapper')) return;
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
  if (e.type === 'pointerup') highlightSelection(tool());
};
document.addEventListener('pointerup', endPenSel);
document.addEventListener('pointercancel', endPenSel);

/* While a finger (or the Pencil) is still selecting, the would-be highlight is drawn in the tool's
   colour: iPadOS may not show the selection itself until the finger is lifted. */
function hidePreview(): void { document.querySelectorAll('.zc-sel-preview').forEach((n) => n.remove()); }
document.addEventListener('selectionchange', () => {
  hidePreview();
  const t = tool();
  if ((t !== 'highlight' && t !== 'underline') || lastPointer === 'mouse') return;
  const got = selectionRects(window.getSelection());
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

// A finger selects with a long press (and the handles); a button then makes the highlight.
let markPill: (HTMLDivElement & { zcRange?: Range }) | null = null;
function hideMarkPill(): void { markPill?.remove(); markPill = null; }
let selTimer: ReturnType<typeof setTimeout> | undefined;
document.addEventListener('selectionchange', () => {
  clearTimeout(selTimer);
  selTimer = setTimeout(() => {
    const t = tool();
    if ((t !== 'highlight' && t !== 'underline') || lastPointer !== 'touch' || penSel) { hideMarkPill(); return; }
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
        highlightSelection(tool());
      });
      document.body.appendChild(markPill);
    }
    markPill.zcRange = sel.getRangeAt(0).cloneRange();
    markPill.innerHTML = `<button data-mark>${t === 'underline' ? 'Underline' : 'Highlight'}</button>`;
    markPill.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - 140))}px`;
    markPill.style.top = `${rect.top - 52 < 50 ? rect.bottom + 52 : rect.top - 52}px`;
  }, 250);
});

/* -------- a selected annotation: a Delete button (there may be no keyboard) */
let selPill: HTMLDivElement | null = null;
function showSelPill(): void {
  selPill?.remove(); selPill = null;
  const g = selectedId && document.querySelector(`.anno-layer [data-id="${selectedId}"]`);
  if (!g) return;
  const r = g.getBoundingClientRect();
  selPill = document.createElement('div');
  selPill.className = 'zc-pill zc-sel-pill';
  selPill.innerHTML = '<button data-del>Delete</button>';
  selPill.addEventListener('pointerdown', (ev) => { ev.preventDefault(); ev.stopPropagation(); });
  selPill.addEventListener('click', () => { const rec = selectedId && byId.get(selectedId); if (rec) remove(rec); });
  selPill.style.left = `${Math.max(8, Math.min(r.right - 70, innerWidth - 90))}px`;
  selPill.style.top = `${r.top - 46 < 50 ? r.bottom + 8 : r.top - 46}px`;
  document.body.appendChild(selPill);
}
document.getElementById('pdf-container')?.addEventListener('scroll', () => { selPill?.remove(); selPill = null; hideMarkPill(); }, { passive: true });

window.addEventListener('keydown', (e) => {
  const target = e.target as HTMLElement;
  const typing = target.isContentEditable || ['INPUT', 'TEXTAREA'].includes(target.tagName);
  const mod = e.ctrlKey || e.metaKey;
  const key = (e.key || '').toLowerCase();
  if ((e.key === 'Backspace' || e.key === 'Delete') && selectedId && !typing) {
    e.preventDefault();
    const rec = byId.get(selectedId);
    if (rec) remove(rec);
  } else if (mod && !typing && ((key === 'z' && e.shiftKey) || key === 'y')) {
    e.preventDefault();
    undo(redoStack, undoStack);
  } else if (mod && !typing && key === 'z') {
    e.preventDefault();
    undo(undoStack, redoStack);
  } else if (e.key === 'Escape' && selectedId) select(null);
});

document.addEventListener('pointerdown', (e) => {
  const target = e.target as Element;
  if (selectedId && !target.closest?.('.anno-layer') && !target.closest?.('.tool-btn') && !target.closest?.('.zc-sel-pill')) select(null);
  if (markPill && !markPill.contains(target)) hideMarkPill();
});

hooks.onSelect = showSelPill;
