// PDF annotations (issue #20): every highlight, underline, box, drawing and text box is its own
// record, saved on its own through /api/attachments/<id>/annotations — so two tabs on one PDF never
// overwrite each other, and the app (and Claude) can read what was highlighted.
//
// Positions are fractions of the page as displayed (0–1, origin top left), sizes are PDF points at
// 100 % zoom; see backend/annotations.py. Each page gets an SVG layer whose viewBox is the page in
// points, so drawings scale with the zoom by themselves. Text boxes are HTML in a layer of their own
// above it (Safari misplaces HTML inside an SVG foreignObject), sized through --zc-scale.
//
// The viewer (viewer.html) owns the toolbar: the current tool is document.body.dataset.zcTool,
// the colour #tool-color and the size #tool-size. It fires 'zc:pagecreated' for each page.

const SVG = 'http://www.w3.org/2000/svg';
const params = new URLSearchParams(location.search);
const paperId = params.get('paper_id');
const file = params.get('file');

const byId = new Map();          // id → record
const layers = new Map();        // page number → svg layer
const textLayers = new Map();    // page number → HTML layer of the text boxes
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

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
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

/** What shows an annotation (an SVG group, or a text box's element). */
const nodeOf = (id) => document.querySelector(`.anno-layer [data-id="${id}"]`);

function draw(rec) {
  const svg = layers.get(rec.page);
  nodeOf(rec.id)?.remove();
  if (!svg) return;
  const [W, H] = pageSize(svg.parentElement);
  if (rec.type === 'text') {
    const [x, y] = rec.rects[0];
    const node = textBox(rec, x * W, y * H, W, H);
    node.dataset.id = rec.id;
    textLayers.get(rec.page).appendChild(node);
    if (selectedId === rec.id) showSelection(node);
    return;
  }
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
  }
  svg.appendChild(g);
  if (selectedId === rec.id) showSelection(g);
}

/** A text box: a contenteditable div with a handle to change the font size, placed at (x, y) points. */
function textBox(rec, x, y, W, H) {
  const node = document.createElement('div');
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
    const move = (m) => setFontSize(input, Math.max(6, start + (m.clientY - startY) / scale()));
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
function setFontSize(input, pt) {
  input.dataset.size = pt;
  input.style.fontSize = `calc(var(--zc-scale, 1.5) * ${pt}px)`;
}

/** Puts a text box at (x, y) points of a page W × H points (as percentages, so it follows the zoom). */
function placeText(node, x, y, W, H) {
  node.zcX = x;
  node.zcY = y;
  node.style.left = `${(x / W) * 100}%`;
  node.style.top = `${(y / H) * 100}%`;
}

/** [x, y, w, h] of a text box as drawn, as page fractions. */
function measure(node) {
  const [W, H] = pageSize(node.closest('.page-wrapper'));
  const r = node.firstElementChild.getBoundingClientRect();
  return [node.zcX / W, node.zcY / H, r.width / scale() / W, r.height / scale() / H]
    .map((v) => Math.round(v * 1e6) / 1e6);
}

function finishText(id, node, input) {
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

function showSelection(node) {
  document.querySelectorAll('.anno-selection').forEach((n) => n.remove());
  const svg = layers.get(Number(node.closest('.page-wrapper')?.dataset.pageNumber));
  if (!svg) return;
  let b;
  if (node.getBBox) b = node.getBBox();
  else {   // a text box: its place on the page, in points
    const r = node.getBoundingClientRect(), sr = svg.getBoundingClientRect(), s = scale();
    b = { x: (r.left - sr.left) / s, y: (r.top - sr.top) / s, width: r.width / s, height: r.height / s };
  }
  const pad = 3;
  svg.appendChild(el('rect', { class: 'anno-selection', x: b.x - pad, y: b.y - pad, width: b.width + 2 * pad, height: b.height + 2 * pad,
    fill: 'none', stroke: '#0066cc', 'stroke-width': 1.5, 'stroke-dasharray': '4 2', 'pointer-events': 'none' }));
}

function select(id) {
  selectedId = id;
  document.querySelectorAll('.anno-selection').forEach((n) => n.remove());
  if (!id) { showSelPill(); return; }
  const node = nodeOf(id);
  if (node) showSelection(node);
  showSelPill();
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
    nodeOf(rec.id)?.remove();
    notify(`The annotation was not saved: ${e.message}`);
  }
  return rec;
}

async function remove(rec, { record = true } = {}) {
  byId.delete(rec.id);
  nodeOf(rec.id)?.remove();
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
/* Mouse, Apple Pencil and fingers all arrive as pointer events (issue #34).
   - Once a pen has been used (remembered), fingers scroll and pinch while the pen draws, highlights
     and erases; before that (a phone, an iPad without a Pencil) fingers draw too.
   - Safari scrolls the page under the Pencil unless its touches are cancelled; touchstart/touchmove
     of a stylus are cancelled while an annotation tool is chosen (see stopPenScroll).
   - Highlight / underline: a mouse selects text as usual; the Pencil drags over the text (the
     selection follows it); a finger selects with a long press and then taps "Highlight". */
let penSeen = false;
try { penSeen = localStorage.getItem('zc.penSeen') === '1'; } catch { /* private mode */ }
document.body.classList.toggle('zc-pen', penSeen);
function notePen(e) {
  if (e.pointerType !== 'pen' || penSeen) return;
  penSeen = true;
  document.body.classList.add('zc-pen');
  try { localStorage.setItem('zc.penSeen', '1'); } catch { /* private mode */ }
}
/** May this pointer draw? Mouse and pen always; a finger only while no pen has been seen. */
const draws = (e) => e.pointerType !== 'touch' || !penSeen;
let lastPointer = 'mouse';
document.addEventListener('pointerdown', (e) => { lastPointer = e.pointerType || 'mouse'; notePen(e); }, true);

function stopPenScroll(e) {
  if (tool() === 'select') return;
  if (![...e.changedTouches].some((t) => t.touchType === 'stylus')) return;
  if (!e.target.closest?.('.page-wrapper')) return;
  // a tap with the text tool must stay a tap (it opens the keyboard); everything else is drawn
  if (tool() === 'text' && e.type === 'touchstart') return;
  e.preventDefault();
}
document.addEventListener('touchstart', stopPenScroll, { passive: false });
document.addEventListener('touchmove', stopPenScroll, { passive: false });

function pointIn(svg, e) {
  const r = svg.getBoundingClientRect();
  const [W, H] = pageSize(svg.parentElement);
  return { x: (e.clientX - r.left) / scale(), y: (e.clientY - r.top) / scale(), W, H };
}

function newTextBox(html, page, p) {
  const draft = { type: 'text', page, rects: [[p.x / p.W, p.y / p.H, 0, 0]], text: '', color: color(), size: size() };
  const box = textBox({ ...draft, id: `draft-${uid()}` }, p.x, p.y, p.W, p.H);
  box.classList.add('anno-draft');
  box.zcDraft = draft;
  html.appendChild(box);
  // focus right away, inside the tap: iPadOS only opens the keyboard (or Scribble) for a focus
  // that happens during the user's gesture
  box.querySelector('.anno-text-input').focus();
}

/** Pointer input on a page: the SVG layer and the text boxes' layer above it share these handlers
    (positions are always taken from the SVG layer, which covers the page as the other one does). */
function setupLayer(svg, html, page) {
  let shape = null;      // box or ink being drawn: {rec, start, points}
  let drag = null;       // text box being moved
  let tap = null;        // text tool: where the pointer went down (a box is made when it comes up)

  const down = (e) => {
    const g = e.target.closest?.('.anno');
    const rec = g && byId.get(g.dataset.id);
    const t = tool();
    if (t === 'eraser') {
      if (rec) { e.stopPropagation(); remove(rec); }
      return;
    }
    if (t === 'select') select(rec ? rec.id : null);
    // a text box: move it by its frame (typing goes to the text itself)
    const textNode = e.target.closest?.('.anno-text');
    if (textNode) {
      e.stopPropagation();
      if (!e.target.isContentEditable && rec && draws(e)) {
        e.preventDefault();
        e.currentTarget.setPointerCapture?.(e.pointerId);
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
    e.currentTarget.setPointerCapture?.(e.pointerId);
    const g2 = el('g', { class: 'anno anno-draft' });
    const node = t === 'draw'
      ? el('path', { d: `M ${p.x} ${p.y}`, stroke: color(), 'stroke-width': size(), fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })
      : el('rect', { x: p.x, y: p.y, width: 0, height: 0, stroke: color(), 'stroke-width': size(), fill: 'none' });
    g2.appendChild(node);
    svg.appendChild(g2);
    shape = { kind: t, g: g2, node, start: p, points: [[p.x / p.W, p.y / p.H]], W: p.W, H: p.H, id: e.pointerId };
  };

  const move = (e) => {
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
        shape.node.setAttribute('x', Math.min(s.x, p.x));
        shape.node.setAttribute('y', Math.min(s.y, p.y));
        shape.node.setAttribute('width', Math.abs(p.x - s.x));
        shape.node.setAttribute('height', Math.abs(p.y - s.y));
      }
    }
  };

  const finish = (e) => {
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
  for (const layer of [svg, html]) {
    layer.addEventListener('pointerdown', down);
    layer.addEventListener('pointermove', move);
    layer.addEventListener('pointerup', finish);
    layer.addEventListener('pointercancel', finish);
  }
}

/* -------- eraser: drag over annotations to remove them (mouse, Pencil; a finger taps) */
let erasing = null;
document.addEventListener('pointerdown', (e) => {
  if (tool() !== 'eraser' || !draws(e) || e.button > 0 || !e.target.closest?.('.page-wrapper')) return;
  erasing = e.pointerId;
  eraseAt(e);
});
document.addEventListener('pointermove', (e) => { if (erasing === e.pointerId) eraseAt(e); });
document.addEventListener('pointerup', (e) => { if (erasing === e.pointerId) erasing = null; });
document.addEventListener('pointercancel', (e) => { if (erasing === e.pointerId) erasing = null; });
function eraseAt(e) {
  for (const n of document.elementsFromPoint(e.clientX, e.clientY)) {
    const g = n.closest?.('.anno-layer .anno:not(.anno-draft)');
    const rec = g && byId.get(g.dataset.id);
    if (rec) { remove(rec); return; }
  }
}

/* -------- highlight / underline */
/** The page of the selected text and its lines, as [x, y, w, h] page fractions; or null. */
function selectionRects(sel) {
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const anchor = sel.anchorNode?.nodeType === 1 ? sel.anchorNode : sel.anchorNode?.parentElement;
  const wrapper = anchor?.closest('.page-wrapper');
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
  const r6 = (v) => Math.round(v * 1e6) / 1e6;
  return { wrapper, rects: rects.map((r) => [r6((r.left - wr.left) / s / W), r6((r.top - wr.top) / s / H), r6(r.width / s / W), r6(r.height / s / H)]) };
}

/** The selected text becomes one annotation of type t, one rect per line. Returns true when made. */
function highlightSelection(t) {
  const sel = window.getSelection();
  const got = attId && selectionRects(sel);
  if (!got) return false;
  const { wrapper, rects: fr } = got;
  const text = sel.toString().replace(/-\n(?=\w)/g, '').replace(/\s+/g, ' ').trim();
  const rec = { type: t, page: Number(wrapper.dataset.pageNumber), rects: fr, text, color: color() };
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
function caretAt(x, y) {
  if (document.caretRangeFromPoint) return document.caretRangeFromPoint(x, y);
  const pos = document.caretPositionFromPoint?.(x, y);
  if (!pos) return null;
  const r = document.createRange();
  r.setStart(pos.offsetNode, pos.offset);
  return r;
}
const inText = (r) => !!r && !!(r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement)?.closest?.('.textLayer');

// The Pencil drags over the text: the selection follows it, and lifting the pen highlights.
let penSel = null;   // { id, start: Range }
document.addEventListener('pointerdown', (e) => {
  const t = tool();
  if ((t !== 'highlight' && t !== 'underline') || e.pointerType !== 'pen' || !e.target.closest?.('.page-wrapper')) return;
  const start = caretAt(e.clientX, e.clientY);
  if (!inText(start)) return;
  e.preventDefault();
  penSel = { id: e.pointerId, start };
  window.getSelection().removeAllRanges();
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
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(r);
});
const endPenSel = (e) => {
  if (!penSel || e.pointerId !== penSel.id) return;
  penSel = null;
  if (e.type === 'pointerup') highlightSelection(tool());
};
document.addEventListener('pointerup', endPenSel);
document.addEventListener('pointercancel', endPenSel);

/* While a finger (or the Pencil) is still selecting, the would-be highlight is drawn in the tool's
   colour: iPadOS may not show the selection itself until the finger is lifted (issue #36). */
function hidePreview() { document.querySelectorAll('.zc-sel-preview').forEach((n) => n.remove()); }
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
let markPill = null;
function hideMarkPill() { markPill?.remove(); markPill = null; }
let selTimer = null;
document.addEventListener('selectionchange', () => {
  clearTimeout(selTimer);
  selTimer = setTimeout(() => {
    const t = tool();
    if ((t !== 'highlight' && t !== 'underline') || lastPointer !== 'touch' || penSel) { hideMarkPill(); return; }
    const sel = window.getSelection();
    const node = sel?.anchorNode?.nodeType === 1 ? sel.anchorNode : sel?.anchorNode?.parentElement;
    if (!sel || sel.isCollapsed || !node?.closest?.('.textLayer')) { hideMarkPill(); return; }
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    if (!markPill) {
      markPill = document.createElement('div');
      markPill.className = 'zc-pill zc-mark-pill';
      markPill.addEventListener('pointerdown', (ev) => ev.preventDefault());
      markPill.addEventListener('click', () => {
        // a tap elsewhere may have cleared the selection by now: put back the one the button was made for
        const sel2 = window.getSelection();
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
let selPill = null;
function showSelPill() {
  selPill?.remove(); selPill = null;
  const g = selectedId && document.querySelector(`.anno-layer [data-id="${selectedId}"]`);
  if (!g) return;
  const r = g.getBoundingClientRect();
  selPill = document.createElement('div');
  selPill.className = 'zc-pill zc-sel-pill';
  selPill.innerHTML = '<button data-del>Delete</button>';
  selPill.addEventListener('pointerdown', (ev) => { ev.preventDefault(); ev.stopPropagation(); });
  selPill.addEventListener('click', () => { const rec = byId.get(selectedId); if (rec) remove(rec); });
  selPill.style.left = `${Math.max(8, Math.min(r.right - 70, innerWidth - 90))}px`;
  selPill.style.top = `${r.top - 46 < 50 ? r.bottom + 8 : r.top - 46}px`;
  document.body.appendChild(selPill);
}
document.getElementById('pdf-container')?.addEventListener('scroll', () => { selPill?.remove(); selPill = null; hideMarkPill(); }, { passive: true });

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

document.addEventListener('pointerdown', (e) => {
  if (selectedId && !e.target.closest?.('.anno-layer') && !e.target.closest?.('.tool-btn') && !e.target.closest?.('.zc-sel-pill')) select(null);
  if (markPill && !markPill.contains(e.target)) hideMarkPill();
});

/* ------------------------------------------------------------------ pages and loading */
function addLayer(wrapper) {
  const page = Number(wrapper.dataset.pageNumber);
  if (layers.get(page)?.isConnected) return;
  const [W, H] = pageSize(wrapper);
  const svg = el('svg', { class: `anno-layer ${modeClass()}`, viewBox: `0 0 ${W} ${H}` });
  svg.style.width = '100%';
  svg.style.height = '100%';
  const html = document.createElement('div');
  html.className = `anno-layer anno-text-layer ${modeClass()}`;
  wrapper.append(svg, html);
  layers.set(page, svg);
  textLayers.set(page, html);
  setupLayer(svg, html, page);
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
  clearDrawn();
  for (const rec of byId.values()) draw(rec);
}

function clearDrawn() {
  document.querySelectorAll('.anno-layer .anno:not(.anno-draft)').forEach((n) => n.remove());
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
    clearDrawn();
    for (const rec of byId.values()) draw(rec);
  } catch { /* offline: keep what is shown */ }
}
window.addEventListener('focus', refresh);

/** Undo / Redo buttons: the keyboard shortcuts are not there on a tablet. */
function addUndoButtons() {
  const tools = document.getElementById('tools-section');
  if (!tools || document.getElementById('anno-undo')) return;
  const mk = (id, title, d, fn) => {
    const b = document.createElement('button');
    b.id = id; b.className = 'icon-btn'; b.title = title;
    b.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
    b.addEventListener('click', fn);
    return b;
  };
  const sep = document.createElement('span');
  sep.className = 'divider';
  tools.prepend(
    mk('anno-undo', 'Undo (⌘Z)', '<path d="M4 6h6.5a3.5 3.5 0 010 7H7"/><path d="M6.5 3.5L4 6l2.5 2.5"/>', () => undo(undoStack, redoStack)),
    mk('anno-redo', 'Redo (⇧⌘Z)', '<path d="M12 6H5.5a3.5 3.5 0 000 7H9"/><path d="M9.5 3.5L12 6 9.5 8.5"/>', () => undo(redoStack, undoStack)),
    sep);
}
addUndoButtons();

document.addEventListener('zc:pagecreated', (e) => addLayer(e.detail.wrapper));
for (const w of document.querySelectorAll('.page-wrapper')) addLayer(w);   // pages laid out before this module ran
const ready = load();

// read-only view for the browser checklists
window.zcAnnotations = { ready, list: () => [...byId.values()].map(clone), attachment: () => attId, refresh };
