// Annotation records of the open PDF, how they are drawn on the pages, and saving each change on
// its own (with undo and redo). See annotations.ts for the overview.
import type { Attachment } from '../../types.js';

/** An annotation as stored (backend/library/annotations.py): positions are page fractions. */
export interface Annotation {
  id: string;
  page: number;
  type: 'highlight' | 'underline' | 'box' | 'ink' | 'text';
  rects?: number[][];      // [x, y, w, h] each
  path?: number[][];       // [x, y] each (ink)
  text?: string;
  color?: string;
  fill?: string | null;    // box: the colour inside (none when null)
  size?: number;           // box: 0 for no border
  comment?: string;
  modified?: string;
}

/** An undo / redo step. */
type Step =
  | { op: 'create' | 'delete'; rec: Annotation }
  | { op: 'update'; id: string; before: Partial<Annotation>; after: Partial<Annotation> };

/** A text box on the page; a new one keeps its record-to-be until it has text. */
export type TextBoxNode = HTMLDivElement & { zcX: number; zcY: number; zcDraft?: Omit<Annotation, 'id'> };

/** Set by the input code: called whenever the selected annotation changes. */
export const hooks = { onSelect: (): void => {} };

export const SVG = 'http://www.w3.org/2000/svg';
export const params = new URLSearchParams(location.search);
export const paperId = params.get('paper_id');
export const file = params.get('file');

export const byId = new Map<string, Annotation>();
export const layers = new Map<number, SVGSVGElement>();         // page number → svg layer
export const textLayers = new Map<number, HTMLElement>();        // page number → HTML layer of the text boxes
export let attId: number | null = params.get('att') ? Number(params.get('att')) : null;
export const undoStack: Step[] = [];
export let redoStack: Step[] = [];
export let selectedId: string | null = null;

/* ------------------------------------------------------------------ helpers */
export const tool = () => document.body.dataset.zcTool || 'select';
export const color = () => (document.getElementById('tool-color') as HTMLInputElement | null)?.value || '#ffff00';
export const size = () => Math.max(1, Number((document.getElementById('tool-size') as HTMLInputElement | null)?.value) || 2);
/** The box tool: is the border drawn, and the fill colour (null: not filled). The toolbar marks a
    part left out with data-none on its colour (see prefs.ts). */
export const border = () => !document.getElementById('color-wrapper')?.hasAttribute('data-none');
export const fill = () => (document.getElementById('fill-wrapper')?.hasAttribute('data-none') ? null
  : (document.getElementById('tool-fill') as HTMLInputElement | null)?.value || null);
export const scale = () => window.zcViewer?.scale || 1.5;
export const uid = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '') : Math.random().toString(36).slice(2) + Date.now().toString(36));
/** An SVG element with these attributes. */
export function el<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}): SVGElementTagNameMap[K] {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  return n;
}
export const clone = <T>(o: T): T => JSON.parse(JSON.stringify(o));

export function wrapperOf(page: number) { return document.querySelector<HTMLElement>(`.page-wrapper[data-page-number="${page}"]`); }
/** A page's size in PDF points. */
export function pageSize(wrapper: Element): [number, number] {
  const d = (wrapper as HTMLElement).dataset;
  return [parseFloat(d.baseWidth!), parseFloat(d.baseHeight!)];
}

export function notify(msg: string): void {
  const t = document.createElement('div');
  t.className = 'zc-toast';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

// This page's id for the change feed: its own edits are not reported back to it.
export const CLIENT = `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;

export async function api(method: string, path: string, body?: unknown): Promise<any> {
  const headers: Record<string, string> = { 'X-Synapse-Client': CLIENT };
  if (body) headers['Content-Type'] = 'application/json';
  const r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(data.error || `Request failed (${r.status})`) as Error & { status: number }; e.status = r.status; throw e; }
  return data;
}

/** The attachment this PDF belongs to: from ?att=, or found by the file URL among the item's PDFs. */
export async function resolveAttachment(): Promise<number | null> {
  if (attId || !paperId || !file) return attId;
  try {
    const item = await api('GET', `/api/items/${paperId}`);
    const hit = ((item.attachments || []) as Attachment[]).find((a) => file === `/pdfs/${encodeURIComponent(a.url)}` || file === `/pdfs/${a.url}`
      || file === `/api/attachments/${a.id}/remote`);
    attId = hit ? hit.id : null;
  } catch { attId = null; }
  return attId;
}

/* ------------------------------------------------------------------ drawing */
export function modeClass(t = tool()): string {
  if (t === 'box' || t === 'draw' || t === 'text') return 'mode-draw';
  if (t === 'highlight' || t === 'underline') return 'mode-highlight';
  return 'mode-select';
}

/** What shows an annotation (an SVG group, or a text box's element). */
export const nodeOf = (id: string) => document.querySelector<HTMLElement | SVGGElement>(`.anno-layer [data-id="${id}"]`);

export function draw(rec: Annotation): void {
  const svg = layers.get(rec.page);
  nodeOf(rec.id)?.remove();
  if (!svg) return;
  const [W, H] = pageSize(svg.parentElement!);
  if (rec.type === 'text') {
    const [x, y] = rec.rects![0];
    const node = textBox(rec, x * W, y * H, W, H);
    node.dataset.id = rec.id;
    textLayers.get(rec.page)!.appendChild(node);
    if (selectedId === rec.id) showSelection(node);
    return;
  }
  const g = el('g', { class: `anno anno-${rec.type}`, 'data-id': rec.id });
  const box = ([x, y, w, h]: number[]) => ({ x: x * W, y: y * H, w: w * W, h: h * H });
  if (rec.type === 'highlight') {
    for (const r of rec.rects!) { const b = box(r); g.appendChild(el('rect', { x: b.x, y: b.y, width: b.w, height: b.h, fill: rec.color || '#ffff00', 'fill-opacity': 0.8 })); }
  } else if (rec.type === 'underline') {
    for (const r of rec.rects!) {
      const b = box(r);
      g.appendChild(el('line', { x1: b.x, y1: b.y + b.h, x2: b.x + b.w, y2: b.y + b.h, stroke: rec.color || '#ff0000', 'stroke-width': rec.size || 2 }));
      g.appendChild(el('rect', { x: b.x, y: b.y, width: b.w, height: b.h, fill: 'transparent' }));
    }
  } else if (rec.type === 'box') {
    const b = box(rec.rects![0]);
    g.appendChild(boxRect(b.x, b.y, b.w, b.h, rec));
  } else if (rec.type === 'ink') {
    const d = rec.path!.map(([x, y], i) => `${i ? 'L' : 'M'} ${x * W} ${y * H}`).join(' ');
    g.appendChild(el('path', { d, stroke: rec.color || '#000000', 'stroke-width': rec.size || 2, fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  }
  svg.appendChild(g);
  if (selectedId === rec.id) showSelection(g);
}

/** A box's rectangle at (x, y) points, w × h points: its border and its fill, which looks like a
    highlight (the same opacity, and the layer multiplies it with the page). */
export function boxRect(x: number, y: number, w: number, h: number, rec: Pick<Annotation, 'color' | 'fill' | 'size'>): SVGRectElement {
  const lined = rec.size !== 0;
  return el('rect', { x, y, width: w, height: h, stroke: lined ? rec.color || '#000000' : 'none', 'stroke-width': lined ? rec.size || 2 : 0,
    fill: rec.fill || 'none', 'fill-opacity': 0.8 });
}

/** A text box: a contenteditable div with a handle to change the font size, placed at (x, y) points. */
export function textBox(rec: Annotation, x: number, y: number, W: number, H: number): TextBoxNode {
  const node = document.createElement('div') as TextBoxNode;
  node.className = 'anno anno-text';
  placeText(node, x, y, W, H);
  const frame = document.createElement('div');
  frame.className = 'anno-text-frame';
  Object.assign(frame.style, { display: 'inline-block', position: 'relative', pointerEvents: 'auto', padding: 'calc(var(--zc-scale, 1.5) * 4px)', cursor: 'move' });
  const input = document.createElement('div');
  input.className = 'anno-text-input';
  input.contentEditable = 'true';
  Object.assign(input.style, { cursor: 'text', color: rec.color || '#000000', fontFamily: 'sans-serif', outline: 'none',
    minWidth: '1ch', minHeight: '1em', lineHeight: '1.2', whiteSpace: 'pre' });
  setFontSize(input, rec.size || 16);
  input.textContent = rec.text || '';
  const handle = document.createElement('div');
  handle.className = 'anno-text-handle';
  // looks: .anno-text-frame / .anno-text-handle in viewer.html (bigger on touch screens)
  frame.append(input, handle);
  node.appendChild(frame);
  frame.addEventListener('mouseenter', () => frame.classList.add('hover'));
  frame.addEventListener('mouseleave', () => frame.classList.remove('hover'));

  input.addEventListener('blur', () => finishText(rec.id, node, input));
  // The size handle: dragged with a mouse, the Pencil or a finger (shown while the box is being edited
  // on touch screens, which have no hover).
  handle.addEventListener('pointerdown', (ev) => {
    ev.stopPropagation();
    ev.preventDefault();
    handle.setPointerCapture?.(ev.pointerId);
    const startY = ev.clientY;
    const start = Number(input.dataset.size);
    const move = (m: PointerEvent) => setFontSize(input, Math.max(6, start + (m.clientY - startY) / scale()));
    const up = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
      const rec2 = byId.get(rec.id);
      if (rec2) change(rec2, { size: Math.round(Number(input.dataset.size) * 10) / 10, rects: [measure(node)] });
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
  });
  return node;
}

/** The font size in points at 100 % zoom; drawn at the current zoom. */
export function setFontSize(input: HTMLElement, pt: number): void {
  input.dataset.size = String(pt);
  input.style.fontSize = `calc(var(--zc-scale, 1.5) * ${pt}px)`;
}

/** Puts a text box at (x, y) points of a page W × H points (as percentages, so it follows the zoom). */
export function placeText(node: TextBoxNode, x: number, y: number, W: number, H: number): void {
  node.zcX = x;
  node.zcY = y;
  node.style.left = `${(x / W) * 100}%`;
  node.style.top = `${(y / H) * 100}%`;
}

/** [x, y, w, h] of a text box as drawn, as page fractions. */
export function measure(node: TextBoxNode): number[] {
  const [W, H] = pageSize(node.closest('.page-wrapper')!);
  const r = node.firstElementChild!.getBoundingClientRect();
  return [node.zcX / W, node.zcY / H, r.width / scale() / W, r.height / scale() / H]
    .map((v) => Math.round(v * 1e6) / 1e6);
}

export function finishText(id: string, node: TextBoxNode, input: HTMLElement): void {
  const text = input.innerText.replace(/\n$/, '');
  const rec = byId.get(id);
  if (!rec) {                                   // a new box: saved once it has text
    const draft = node.zcDraft;
    if (!text.trim() || !draft) { node.remove(); return; }
    const rects = [measure(node)];   // while it is still on the page
    node.remove();
    create({ ...draft, text, rects });
    return;
  }
  if (!text.trim()) { remove(rec); return; }
  if (text !== rec.text) change(rec, { text, rects: [measure(node)] });
}

export function showSelection(node: Element): void {
  document.querySelectorAll('.anno-selection').forEach((n) => n.remove());
  const svg = layers.get(Number(node.closest<HTMLElement>('.page-wrapper')?.dataset.pageNumber));
  if (!svg) return;
  let b: { x: number; y: number; width: number; height: number };
  if (node instanceof SVGGraphicsElement) b = node.getBBox();
  else {   // a text box: its place on the page, in points
    const r = node.getBoundingClientRect(), sr = svg.getBoundingClientRect(), s = scale();
    b = { x: (r.left - sr.left) / s, y: (r.top - sr.top) / s, width: r.width / s, height: r.height / s };
  }
  const pad = 3;
  svg.appendChild(el('rect', { class: 'anno-selection', x: b.x - pad, y: b.y - pad, width: b.width + 2 * pad, height: b.height + 2 * pad,
    fill: 'none', stroke: '#0066cc', 'stroke-width': 1.5, 'stroke-dasharray': '4 2', 'pointer-events': 'none' }));
}

export function select(id: string | null): void {
  selectedId = id;
  document.querySelectorAll('.anno-selection').forEach((n) => n.remove());
  if (!id) { hooks.onSelect(); return; }
  const node = nodeOf(id);
  if (node) showSelection(node);
  hooks.onSelect();
}

/* ------------------------------------------------------------------ changes (each saved on its own) */
export async function create(draft: Omit<Annotation, 'id'> & { id?: string }, { record = true } = {}): Promise<Annotation> {
  const rec = { ...draft, id: draft.id || uid() } as Annotation;
  byId.set(rec.id, rec);
  draw(rec);
  if (record) { undoStack.push({ op: 'create', rec: clone(rec) }); redoStack = []; }
  try {
    const res = await api('POST', `/api/attachments/${attId}/annotations`, rec);
    byId.set(rec.id, res.annotation);
  } catch (e) {
    byId.delete(rec.id);
    nodeOf(rec.id)?.remove();
    notify(`The annotation was not saved: ${(e as Error).message}`);
  }
  return rec;
}

export async function remove(rec: Annotation, { record = true } = {}): Promise<void> {
  byId.delete(rec.id);
  nodeOf(rec.id)?.remove();
  if (selectedId === rec.id) select(null);
  if (record) { undoStack.push({ op: 'delete', rec: clone(rec) }); redoStack = []; }
  try { await api('DELETE', `/api/annotations/${rec.id}`); } catch (e) {
    if ((e as { status?: number }).status !== 404) notify(`Not deleted: ${(e as Error).message}`);
  }
}

export async function change(rec: Annotation, fields: Partial<Annotation>, { record = true } = {}): Promise<void> {
  const before = Object.fromEntries(Object.keys(fields).map((k) => [k, clone(rec[k as keyof Annotation] ?? null)]));
  const after = { ...rec, ...fields };
  byId.set(rec.id, after);
  draw(after);
  if (record) { undoStack.push({ op: 'update', id: rec.id, before, after: clone(fields) }); redoStack = []; }
  try {
    const res = await api('PATCH', `/api/annotations/${rec.id}`, fields);
    byId.set(rec.id, res.annotation);
  } catch (e) { notify(`Not saved: ${(e as Error).message}`); }
}

export async function undo(fromStack: Step[], toStack: Step[]): Promise<void> {
  const step = fromStack.pop();
  if (!step) return;
  toStack.push(step);
  if (step.op === 'create') {
    const rec = byId.get(step.rec.id);
    if (fromStack === undoStack) { if (rec) await remove(rec, { record: false }); } else await create(step.rec, { record: false });
  } else if (step.op === 'delete') {
    if (fromStack === undoStack) await create(step.rec, { record: false }); else { const rec = byId.get(step.rec.id); if (rec) await remove(rec, { record: false }); }
  } else if (step.op === 'update') {
    const rec = byId.get(step.id);
    if (rec) await change(rec, fromStack === undoStack ? step.before : step.after, { record: false });
  }
}

