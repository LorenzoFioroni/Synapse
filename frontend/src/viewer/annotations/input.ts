// Pointer, Pencil and touch input for the annotation tools: drawing boxes (with a border, a fill
// or both) and ink, text boxes, the eraser, and the controls (colours, Delete) and keyboard
// shortcuts for a selected annotation. Highlighting text is in textMarks.ts. See annotations.ts
// for the overview.
import { holdPointer, type SwatchRow, swatchRow } from './colorMenu.js';
import {
  type Annotation, type TextBoxNode, border, boxRect, byId, change, color, create, draw, el, fill, hooks, measure, pageSize,
  placeText, redoStack, remove, scale, select, selectedId, size, textBox, tool, uid, undo, undoStack,
} from './model.js';
import { draws } from './pointer.js';
import { textIn } from './textMarks.js';

/** A point on a page in PDF points, with the page's size. */
interface Point { x: number; y: number; W: number; H: number }
const r6 = (v: number) => Math.round(v * 1e6) / 1e6;

/* ------------------------------------------------------------------ input */
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
  // box or ink being drawn (a box keeps the colours it was started with)
  let shape: { kind: 'box' | 'draw'; style?: Pick<Annotation, 'color' | 'fill' | 'size'>; g: SVGGElement; node: SVGPathElement | SVGRectElement; start: Point;
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
    const style = t === 'box' ? { color: color(), fill: fill(), size: border() ? size() : 0 } : undefined;
    const node = style
      ? boxRect(p.x, p.y, 0, 0, style)
      : el('path', { d: `M ${p.x} ${p.y}`, stroke: color(), 'stroke-width': size(), fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
    g2.appendChild(node);
    svg.appendChild(g2);
    shape = { kind: t as 'box' | 'draw', style, g: g2, node, start: p, points: [[p.x / p.W, p.y / p.H]], W: p.W, H: p.H, id: e.pointerId };
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
      const rect = [r6(x / s.W), r6(y / s.H), r6(w / s.W), r6(h / s.H)];
      create({ type: 'box', page, rects: [rect], ...s.style, text: textIn(svg.parentElement!, rect) });
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

/* -------- a selected annotation: its colour and a Delete button (there may be no keyboard) */
let selPill: HTMLDivElement | null = null;
function hideSelPill(): void { selPill?.remove(); selPill = null; }

/** The colours of a selected annotation that can be changed: a box's border and fill (either may
    be left out, not both), or the one colour of the others. */
function colorRows(rec: Annotation): SwatchRow[] {
  const preview = (part: Partial<Annotation>) => draw({ ...(byId.get(rec.id) || rec), ...part });
  if (rec.type !== 'box') {
    return [{ value: rec.color || null, onPick: (c) => c && change(rec, { color: c }), onPreview: (c) => preview({ color: c }) }];
  }
  const lined = rec.size !== 0;
  return [
    { label: 'Border', value: lined ? rec.color || '#000000' : null, none: rec.fill ? true : 'disabled',
      onPick: (c) => change(rec, c ? { color: c, size: rec.size || 2 } : { size: 0 }),
      onPreview: (c) => preview({ color: c, size: rec.size || 2 }) },
    { label: 'Fill', value: rec.fill || null, none: lined ? true : 'disabled',
      onPick: (c) => change(rec, { fill: c }), onPreview: (c) => preview({ fill: c }) },
  ];
}

function showSelPill(): void {
  hideSelPill();
  const g = selectedId && document.querySelector(`.anno-layer [data-id="${selectedId}"]`);
  const rec = selectedId ? byId.get(selectedId) : undefined;
  if (!g || !rec) return;
  const r = g.getBoundingClientRect();
  selPill = document.createElement('div');
  selPill.className = 'zc-pill zc-sel-pill';
  const rows = document.createElement('div');
  rows.className = 'zc-swatch-rows';
  for (const row of colorRows(rec)) {
    // after a change, the controls are made again for the annotation as it is now
    rows.appendChild(swatchRow({ ...row, onPick: async (c) => { if (c === row.value) return; await row.onPick(c); showSelPill(); } }));
  }
  const sep = document.createElement('span');
  sep.className = 'zc-pill-sep';
  const del = document.createElement('button');
  del.dataset.del = '';
  del.textContent = 'Delete';
  del.addEventListener('click', () => { const cur = selectedId && byId.get(selectedId); if (cur) remove(cur); });
  selPill.append(rows, sep, del);
  holdPointer(selPill);
  document.body.appendChild(selPill);
  const w = selPill.getBoundingClientRect();
  selPill.style.left = `${Math.max(8, Math.min(r.right - w.width, innerWidth - w.width - 8))}px`;
  selPill.style.top = `${r.top - w.height - 8 < 50 ? r.bottom + 8 : r.top - w.height - 8}px`;
}
document.getElementById('pdf-container')?.addEventListener('scroll', hideSelPill, { passive: true });

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
});

hooks.onSelect = showSelPill;
