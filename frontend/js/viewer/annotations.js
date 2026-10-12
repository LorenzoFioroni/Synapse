// PDF annotations (issue #20): every highlight, underline, box, drawing and text box is its own
// record, saved on its own through /api/attachments/<id>/annotations — so two tabs on one PDF never
// overwrite each other, and the app (and Claude) can read what was highlighted.
//
// Positions are fractions of the page as displayed (0–1, origin top left), sizes are PDF points at
// 100 % zoom; see backend/annotations.py. Each page gets an SVG layer whose viewBox is the page in
// points, so drawings scale with the zoom by themselves.
//
// The viewer (viewer.html) owns the toolbar: the current tool is document.body.dataset.zcTool,
// the colour #tool-color and the size #tool-size. It fires 'zc:pagecreated' for each page.

const SVG = 'http://www.w3.org/2000/svg';
const params = new URLSearchParams(location.search);
const paperId = params.get('paper_id');
const file = params.get('file');

const byId = new Map();          // id → record
const layers = new Map();        // page number → svg layer
let attId = params.get('att') ? Number(params.get('att')) : null;
let undoStack = [];
let redoStack = [];
let selectedId = null;
let loaded = false;

/* ------------------------------------------------------------------ helpers */
const tool = () => document.body.dataset.zcTool || 'select';
const color = () => document.getElementById('tool-color')?.value || '#ffff00';
const size = () => Math.max(1, Number(document.getElementById('tool-size')?.value) || 2);
const scale = () => window.zcViewer?.scale || 1.5;
const uid = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '') : Math.random().toString(36).slice(2) + Date.now().toString(36));
const el = (tag, attrs = {}) => { const n = document.createElementNS(SVG, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
const clone = (o) => JSON.parse(JSON.stringify(o));

function wrapperOf(page) { return document.querySelector(`.page-wrapper[data-page-number="${page}"]`); }
function pageSize(wrapper) { return [parseFloat(wrapper.dataset.baseWidth), parseFloat(wrapper.dataset.baseHeight)]; }

function notify(msg) {
  const t = document.createElement('div');
  t.className = 'zc-toast';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

// This page's id for the change feed (#34): its own edits are not reported back to it.
const CLIENT = `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;

async function api(method, path, body) {
  const headers = { 'X-Synapse-Client': CLIENT };
  if (body) headers['Content-Type'] = 'application/json';
  const r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(data.error || `Request failed (${r.status})`); e.status = r.status; throw e; }
  return data;
}

/** The attachment this PDF belongs to: from ?att=, or found by the file URL among the item's PDFs. */
async function resolveAttachment() {
  if (attId || !paperId || !file) return attId;
  try {
    const item = await api('GET', `/api/items/${paperId}`);
    const hit = (item.attachments || []).find((a) => file === `/pdfs/${encodeURIComponent(a.url)}` || file === `/pdfs/${a.url}`
      || file === `/api/attachments/${a.id}/remote`);
    attId = hit ? hit.id : null;
  } catch { attId = null; }
  return attId;
}

/* ------------------------------------------------------------------ drawing */
function modeClass(t = tool()) {
  if (t === 'box' || t === 'draw' || t === 'text') return 'mode-draw';
  if (t === 'highlight' || t === 'underline') return 'mode-highlight';
  return 'mode-select';
}

function draw(rec) {
  const svg = layers.get(rec.page);
  svg?.querySelector(`[data-id="${rec.id}"]`)?.remove();
  if (!svg) return;
  const [W, H] = pageSize(svg.parentElement);
  const g = el('g', { class: `anno anno-${rec.type}`, 'data-id': rec.id });
  const box = ([x, y, w, h]) => ({ x: x * W, y: y * H, w: w * W, h: h * H });
  if (rec.type === 'highlight') {
    for (const r of rec.rects) { const b = box(r); g.appendChild(el('rect', { x: b.x, y: b.y, width: b.w, height: b.h, fill: rec.color || '#ffff00', 'fill-opacity': 0.8 })); }
  } else if (rec.type === 'underline') {
    for (const r of rec.rects) {
      const b = box(r);
      g.appendChild(el('line', { x1: b.x, y1: b.y + b.h, x2: b.x + b.w, y2: b.y + b.h, stroke: rec.color || '#ff0000', 'stroke-width': rec.size || 2 }));
      g.appendChild(el('rect', { x: b.x, y: b.y, width: b.w, height: b.h, fill: 'transparent' }));
    }
  } else if (rec.type === 'box') {
    const b = box(rec.rects[0]);
    g.appendChild(el('rect', { x: b.x, y: b.y, width: b.w, height: b.h, stroke: rec.color || '#000000', 'stroke-width': rec.size || 2, fill: 'none' }));
  } else if (rec.type === 'ink') {
    const d = rec.path.map(([x, y], i) => `${i ? 'L' : 'M'} ${x * W} ${y * H}`).join(' ');
    g.appendChild(el('path', { d, stroke: rec.color || '#000000', 'stroke-width': rec.size || 2, fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  } else if (rec.type === 'text') {
    const b = box(rec.rects[0]);
    g.appendChild(textBox(rec, b.x, b.y));
  }
  svg.appendChild(g);
  if (selectedId === rec.id) showSelection(g);
}

/** A text box: a contenteditable div in a foreignObject, with a handle to change the font size. */
function textBox(rec, x, y) {
  const fo = el('foreignObject', { x, y, width: 1000, height: 1000 });
  fo.style.pointerEvents = 'none';
  const frame = document.createElement('div');
  frame.className = 'anno-text-frame';
  Object.assign(frame.style, { display: 'inline-block', position: 'relative', pointerEvents: 'auto', padding: '4px', cursor: 'move', border: '1px dotted transparent' });
  const input = document.createElement('div');
  input.className = 'anno-text-input';
  input.contentEditable = 'true';
  Object.assign(input.style, { cursor: 'text', color: rec.color || '#000000', fontSize: `${rec.size || 16}px`, fontFamily: 'sans-serif', outline: 'none',
    minWidth: '1ch', minHeight: '1em', lineHeight: '1.2', whiteSpace: 'pre' });
  input.textContent = rec.text || '';
  const handle = document.createElement('div');
  handle.className = 'anno-text-handle';
  Object.assign(handle.style, { position: 'absolute', right: '-4px', bottom: '-4px', width: '8px', height: '8px', background: '#aaa',
    cursor: 'nwse-resize', borderRadius: '50%', opacity: '0', transition: 'opacity .2s' });
  frame.append(input, handle);
  fo.appendChild(frame);
  frame.addEventListener('mouseenter', () => { frame.style.border = '1px dotted #aaa'; handle.style.opacity = '1'; });
  frame.addEventListener('mouseleave', () => { frame.style.border = '1px dotted transparent'; handle.style.opacity = '0'; });

  input.addEventListener('blur', () => finishText(rec.id, fo, input));
  handle.addEventListener('mousedown', (ev) => {
    ev.stopPropagation();
    ev.preventDefault();
    const startY = ev.clientY;
    const start = parseFloat(input.style.fontSize);
    const move = (m) => { input.style.fontSize = `${Math.max(6, start + (m.clientY - startY) / scale())}px`; };
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      const rec2 = byId.get(rec.id);
      if (rec2) change(rec2, { size: Math.round(parseFloat(input.style.fontSize) * 10) / 10, rects: [measure(fo, rec2)] });
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });
  return fo;
}

/** [x, y, w, h] of a text box as drawn, as page fractions. */
function measure(fo, rec) {
  const svg = fo.ownerSVGElement;
  const [W, H] = pageSize(svg.parentElement);
  const r = fo.firstElementChild.getBoundingClientRect();
  return [parseFloat(fo.getAttribute('x')) / W, parseFloat(fo.getAttribute('y')) / H, r.width / scale() / W, r.height / scale() / H]
    .map((v) => Math.round(v * 1e6) / 1e6);
}

function finishText(id, fo, input) {
  const text = input.innerText.replace(/\n$/, '');
  const rec = byId.get(id);
  if (!rec) {                                   // a new box: saved once it has text
    if (!text.trim()) { fo.closest('g')?.remove(); return; }
    const draft = fo.closest('g')?.zcDraft;
    if (!draft) return;
    const rects = [measure(fo, draft)];   // while it is still on the page
    fo.closest('g').remove();
    create({ ...draft, text, rects });
    return;
  }
  if (!text.trim()) { remove(rec); return; }
  if (text !== rec.text) change(rec, { text, rects: [measure(fo, rec)] });
}

function showSelection(g) {
  document.querySelectorAll('.anno-selection').forEach((n) => n.remove());
  const svg = g.ownerSVGElement;
  if (!svg) return;
  const b = g.getBBox();
  const pad = 3;
  svg.appendChild(el('rect', { class: 'anno-selection', x: b.x - pad, y: b.y - pad, width: b.width + 2 * pad, height: b.height + 2 * pad,
    fill: 'none', stroke: '#0066cc', 'stroke-width': 1.5, 'stroke-dasharray': '4 2', 'pointer-events': 'none' }));
}

function select(id) {
  selectedId = id;
  document.querySelectorAll('.anno-selection').forEach((n) => n.remove());
  if (!id) return;
  const g = document.querySelector(`.anno-layer [data-id="${id}"]`);
  if (g) showSelection(g);
}

/* ------------------------------------------------------------------ changes (each saved on its own) */
async function create(rec, { record = true } = {}) {
  rec = { id: rec.id || uid(), ...rec };
  byId.set(rec.id, rec);
  draw(rec);
  if (record) { undoStack.push({ op: 'create', rec: clone(rec) }); redoStack = []; }
  try {
    const res = await api('POST', `/api/attachments/${attId}/annotations`, rec);
    byId.set(rec.id, res.annotation);
  } catch (e) {
    byId.delete(rec.id);
    document.querySelector(`.anno-layer [data-id="${rec.id}"]`)?.remove();
    notify(`The annotation was not saved: ${e.message}`);
  }
  return rec;
}

async function remove(rec, { record = true } = {}) {
  byId.delete(rec.id);
  document.querySelector(`.anno-layer [data-id="${rec.id}"]`)?.remove();
  if (selectedId === rec.id) select(null);
  if (record) { undoStack.push({ op: 'delete', rec: clone(rec) }); redoStack = []; }
  try { await api('DELETE', `/api/annotations/${rec.id}`); } catch (e) { if (e.status !== 404) notify(`Not deleted: ${e.message}`); }
}

async function change(rec, fields, { record = true } = {}) {
  const before = Object.fromEntries(Object.keys(fields).map((k) => [k, clone(rec[k] ?? null)]));
  const after = { ...rec, ...fields };
  byId.set(rec.id, after);
  draw(after);
  if (record) { undoStack.push({ op: 'update', id: rec.id, before, after: clone(fields) }); redoStack = []; }
  try {
    const res = await api('PATCH', `/api/annotations/${rec.id}`, fields);
    byId.set(rec.id, res.annotation);
  } catch (e) { notify(`Not saved: ${e.message}`); }
}

async function undo(fromStack, toStack) {
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

/* ------------------------------------------------------------------ input */
function pointIn(svg, e) {
  const r = svg.getBoundingClientRect();
  const [W, H] = pageSize(svg.parentElement);
  return { x: (e.clientX - r.left) / scale(), y: (e.clientY - r.top) / scale(), W, H };
}

function setupLayer(svg, page) {
  let shape = null;      // box or ink being drawn: {rec, start, points}
  let drag = null;       // text box being moved

  svg.addEventListener('mousedown', (e) => {
    const g = e.target.closest?.('g.anno');
    const rec = g && byId.get(g.dataset.id);
    const t = tool();
    if (t === 'eraser') {
      if (rec) { e.stopPropagation(); remove(rec); }
      return;
    }
    if (t === 'select') select(rec ? rec.id : null);
    // a text box: move it by its frame (typing goes to the text itself)
    const fo = e.target.closest?.('foreignObject');
    if (fo) {
      e.stopPropagation();
      if (!e.target.isContentEditable && rec) {
        const p = pointIn(svg, e);
        drag = { rec, fo, dx: p.x - parseFloat(fo.getAttribute('x')), dy: p.y - parseFloat(fo.getAttribute('y')), moved: false };
      }
      return;
    }
    if (!['box', 'draw', 'text'].includes(t)) return;
    const p = pointIn(svg, e);
    if (t === 'text') {
      e.preventDefault();
      const draft = { type: 'text', page, rects: [[p.x / p.W, p.y / p.H, 0, 0]], text: '', color: color(), size: size() };
      const g2 = el('g', { class: 'anno anno-text anno-draft' });
      g2.zcDraft = draft;
      const box = textBox({ ...draft, id: `draft-${uid()}` }, p.x, p.y);
      g2.appendChild(box);
      svg.appendChild(g2);
      setTimeout(() => box.querySelector('.anno-text-input').focus(), 10);
      return;
    }
    const g2 = el('g', { class: 'anno anno-draft' });
    const node = t === 'draw'
      ? el('path', { d: `M ${p.x} ${p.y}`, stroke: color(), 'stroke-width': size(), fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })
      : el('rect', { x: p.x, y: p.y, width: 0, height: 0, stroke: color(), 'stroke-width': size(), fill: 'none' });
    g2.appendChild(node);
    svg.appendChild(g2);
    shape = { kind: t, g: g2, node, start: p, points: [[p.x / p.W, p.y / p.H]], W: p.W, H: p.H };
  });

  svg.addEventListener('mousemove', (e) => {
    const p = pointIn(svg, e);
    if (drag) {
      drag.fo.setAttribute('x', p.x - drag.dx);
      drag.fo.setAttribute('y', p.y - drag.dy);
      drag.moved = true;
      return;
    }
    if (!shape) return;
    if (shape.kind === 'draw') {
      shape.points.push([p.x / p.W, p.y / p.H]);
      shape.node.setAttribute('d', `${shape.node.getAttribute('d')} L ${p.x} ${p.y}`);
    } else {
      const s = shape.start;
      shape.node.setAttribute('x', Math.min(s.x, p.x));
      shape.node.setAttribute('y', Math.min(s.y, p.y));
      shape.node.setAttribute('width', Math.abs(p.x - s.x));
      shape.node.setAttribute('height', Math.abs(p.y - s.y));
    }
  });

  const finish = () => {
    if (drag) {
      const { rec, fo, moved } = drag;
      drag = null;
      if (moved) change(rec, { rects: [measure(fo, rec)] });
    }
    if (!shape) return;
    const s = shape;
    shape = null;
    s.g.remove();
    const r6 = (v) => Math.round(v * 1e6) / 1e6;
    if (s.kind === 'draw') {
      if (s.points.length < 2) return;
      create({ type: 'ink', page, path: s.points.map(([x, y]) => [r6(x), r6(y)]), color: s.node.getAttribute('stroke'), size: Number(s.node.getAttribute('stroke-width')) });
    } else {
      const [x, y, w, h] = ['x', 'y', 'width', 'height'].map((k) => parseFloat(s.node.getAttribute(k)));
      if (w < 2 && h < 2) return;   // a click, not a box
      create({ type: 'box', page, rects: [[r6(x / s.W), r6(y / s.H), r6(w / s.W), r6(h / s.H)]], color: s.node.getAttribute('stroke'), size: Number(s.node.getAttribute('stroke-width')) });
    }
  };
  window.addEventListener('mouseup', finish);
}

/** Highlight / underline: the selected text becomes one annotation, one rect per line. */
document.addEventListener('mouseup', () => {
  const t = tool();
  if (t !== 'highlight' && t !== 'underline') return;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return;
  const anchor = sel.anchorNode?.nodeType === 1 ? sel.anchorNode : sel.anchorNode?.parentElement;
  const wrapper = anchor?.closest('.page-wrapper');
  if (!wrapper || !attId) return;
  const raw = [...sel.getRangeAt(0).getClientRects()];
  // keep the innermost text boxes (drop paragraph-sized wrappers and duplicates)
  const rects = raw.filter((a, i) => a.width >= 1 && a.height >= 1 && !raw.some((b, j) => {
    if (i === j) return false;
    const same = Math.abs(a.left - b.left) < 1 && Math.abs(a.right - b.right) < 1 && Math.abs(a.top - b.top) < 1 && Math.abs(a.bottom - b.bottom) < 1;
    if (same) return i > j;
    return a.left <= b.left + 1 && a.right >= b.right - 1 && a.top <= b.top + 1 && a.bottom >= b.bottom - 1;
  }));
  if (!rects.length) return;
  const wr = wrapper.getBoundingClientRect();
  const [W, H] = pageSize(wrapper);
  const s = scale();
  const r6 = (v) => Math.round(v * 1e6) / 1e6;
  const fr = rects.map((r) => [r6((r.left - wr.left) / s / W), r6((r.top - wr.top) / s / H), r6(r.width / s / W), r6(r.height / s / H)]);
  const text = sel.toString().replace(/-\n(?=\w)/g, '').replace(/\s+/g, ' ').trim();
  const rec = { type: t, page: Number(wrapper.dataset.pageNumber), rects: fr, text, color: color() };
  if (t === 'underline') rec.size = size();
  sel.removeAllRanges();
  create(rec);
});

window.addEventListener('keydown', (e) => {
  const typing = e.target.isContentEditable || ['INPUT', 'TEXTAREA'].includes(e.target.tagName);
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

document.addEventListener('mousedown', (e) => {
  if (selectedId && !e.target.closest?.('.anno-layer') && !e.target.closest?.('.tool-btn')) select(null);
});

/* ------------------------------------------------------------------ pages and loading */
function addLayer(wrapper) {
  const page = Number(wrapper.dataset.pageNumber);
  if (layers.get(page)?.isConnected) return;
  const [W, H] = pageSize(wrapper);
  const svg = el('svg', { class: `anno-layer ${modeClass()}`, viewBox: `0 0 ${W} ${H}` });
  svg.style.width = '100%';
  svg.style.height = '100%';
  wrapper.appendChild(svg);
  layers.set(page, svg);
  setupLayer(svg, page);
  for (const rec of byId.values()) if (rec.page === page) draw(rec);
}

async function load() {
  if (!(await resolveAttachment())) { loaded = true; return; }
  try {
    const res = await api('GET', `/api/attachments/${attId}/annotations`);
    byId.clear();
    for (const a of res.annotations) byId.set(a.id, a);
  } catch (e) { notify(`Annotations could not be loaded: ${e.message}`); }
  loaded = true;
  for (const svg of layers.values()) svg.querySelectorAll('g.anno:not(.anno-draft)').forEach((g) => g.remove());
  for (const rec of byId.values()) draw(rec);
}

/** Back in this tab: pick up what another tab changed on the same PDF. */
async function refresh() {
  if (!attId || document.activeElement?.isContentEditable) return;
  try {
    const res = await api('GET', `/api/attachments/${attId}/annotations`);
    const sig = (list) => list.map((a) => `${a.id}:${a.modified}`).sort().join('|');
    if (sig(res.annotations) === sig([...byId.values()])) return;
    byId.clear();
    for (const a of res.annotations) byId.set(a.id, a);
    for (const svg of layers.values()) svg.querySelectorAll('g.anno:not(.anno-draft)').forEach((g) => g.remove());
    for (const rec of byId.values()) draw(rec);
  } catch { /* offline: keep what is shown */ }
}
window.addEventListener('focus', refresh);

/* Annotations made on another device (or in another tab) appear without a reload (#34). */
let feed = { seq: undefined, epoch: undefined };
async function pollChanges() {
  if (document.hidden || !attId) return;
  try {
    const q = new URLSearchParams(feed.seq === undefined ? {} : { since: feed.seq, epoch: feed.epoch });
    const res = await api('GET', `/api/changes?${q}`);
    const first = feed.seq === undefined;
    feed = { seq: res.seq, epoch: res.epoch };
    if (!first && (res.changes || []).includes('annotations')) refresh();
  } catch { /* try again next time */ }
}
setInterval(pollChanges, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) pollChanges(); });

document.addEventListener('zc:pagecreated', (e) => addLayer(e.detail.wrapper));
for (const w of document.querySelectorAll('.page-wrapper')) addLayer(w);   // pages laid out before this module ran
const ready = load();
ready.then(pollChanges);   // start the change feed from here

// read-only view for the browser checklists
window.zcAnnotations = { ready, list: () => [...byId.values()].map(clone), attachment: () => attId, refresh };
