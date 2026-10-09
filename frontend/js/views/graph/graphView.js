// The Graph workspace: papers, the works they cite and your notes, as one network.
//
//   paper ●  an item in the library          cites  paper → paper / cited work (reference lists)
//   work  ○  cited, not in the library       cite   note → paper ([@key])
//   note  ◆  a Markdown note                 link   note → note ([[link]])
//                                            about  paper note → its paper
//
// Left: what to show (kinds, how often a work must be cited, folder scope, search). Middle: the
// canvas (drag to pan, wheel to zoom, drag a node to move it, hover to see its neighbours, click
// to select, double-click to open). Right: the selected node, with everything it connects to.
// "Focus" shows only the neighbourhood of one node (a local graph), N steps deep.
//
// Data: /api/graph (backend/graph.py). Filtering is model.js, the layout is layout.js.

import * as A from '../../actions.js';
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { navPush } from '../../nav.js';
import { emit, folderDescendants, folderTree, state } from '../../store.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml as esc, h, plural, shortAuthors, storage, store } from '../../util.js';
import { fetchReferencesBulk } from '../referencesSection.js';
import { ForceLayout } from './layout.js';
import { DEFAULT_OPTIONS, buildView, label, matchNodes, radius, relations } from './model.js';

let root, canvas, ctx, tip, details, focusBar, statsEl, searchInput;
let data = null;               // /api/graph
let view = { nodes: [], edges: [], degree: new Map() };
let layout = null;
let positions = new Map();     // id → {x, y}, kept across filter changes and reloads of the data
let byId = new Map();
let opts = loadOptions();
let cam = { x: 0, y: 0, k: 1 };    // screen = world * k + (x, y)
let hover = null, selected = null, matches = new Set();
let neighbours = new Set();        // of the hovered (else selected) node
let raf = 0, userMoved = false, open = false;
let colors = {};

export const graphOpen = () => open;

/* ------------------------------------------------------------------ options */
function loadOptions() {
  const s = storage('zc.graph.options', {});
  return { ...DEFAULT_OPTIONS, ...s, edges: { ...DEFAULT_OPTIONS.edges, ...(s.edges || {}) }, folders: null, focus: null, folder: s.folder ?? null };
}

function saveOptions() {
  const { focus, folders, ...keep } = opts;   // focus is per visit (it's in the history entry)
  store('zc.graph.options', keep);
}

/* -------------------------------------------------------------------- setup */
export function initGraph() {
  root = h(`<div id="graph-view" hidden>
    <aside class="graph-controls" aria-label="Graph options">
      <div class="graph-search">${icon('search', 'icon-sm')}<input class="input" type="search" placeholder="Find in graph" spellcheck="false" aria-label="Find in graph"></div>
      <section>
        <h4>Show</h4>
        <label class="graph-opt"><input type="checkbox" data-opt="papers"><span class="dot paper"></span>Papers in your library</label>
        <label class="graph-opt"><input type="checkbox" data-opt="works"><span class="dot work"></span>Cited works not in your library</label>
        <label class="graph-sub">cited by at least <input class="input" type="number" min="1" max="50" data-opt="minCited"> of your papers</label>
        <label class="graph-opt"><input type="checkbox" data-opt="notes"><span class="dot note"></span>Notes</label>
      </section>
      <section>
        <h4>Links</h4>
        <label class="graph-opt"><input type="checkbox" data-edge="cites"><span class="line cites"></span>Paper cites paper</label>
        <label class="graph-opt"><input type="checkbox" data-edge="cite"><span class="line cite"></span>Note cites paper</label>
        <label class="graph-opt"><input type="checkbox" data-edge="link"><span class="line link"></span>Note links to note</label>
        <label class="graph-opt"><input type="checkbox" data-edge="about"><span class="line about"></span>Paper note of a paper</label>
      </section>
      <section>
        <h4>Scope</h4>
        <select class="input graph-folder" aria-label="Folder"></select>
        <label class="graph-opt"><input type="checkbox" data-opt="hideIsolated">Hide unconnected</label>
      </section>
      <section class="graph-stats"></section>
    </aside>
    <section class="graph-main">
      <div class="graph-focus" hidden></div>
      <canvas class="graph-canvas" tabindex="0" aria-label="Graph"></canvas>
      <div class="graph-tip" hidden></div>
      <div class="graph-zoom">
        <button class="icon-btn" data-z="in" title="Zoom in">${icon('plus', 'icon-sm')}</button>
        <button class="icon-btn" data-z="out" title="Zoom out">${icon('minus', 'icon-sm')}</button>
        <button class="icon-btn" data-z="fit" title="Fit to window (F)">${icon('columns', 'icon-sm')}</button>
      </div>
      <div class="graph-empty empty-state" hidden></div>
    </section>
    <aside class="graph-details" aria-label="Selected"></aside>
  </div>`);
  document.getElementById('app').appendChild(root);
  canvas = root.querySelector('canvas');
  ctx = canvas.getContext('2d');
  tip = root.querySelector('.graph-tip');
  details = root.querySelector('.graph-details');
  focusBar = root.querySelector('.graph-focus');
  statsEl = root.querySelector('.graph-stats');
  searchInput = root.querySelector('.graph-search input');

  paintControls();
  root.querySelector('.graph-controls').addEventListener('change', onControl);
  searchInput.addEventListener('input', () => { updateMatches(); draw(); });
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { const first = view.nodes.find((n) => matches.has(n.id)); if (first) { select(first.id); centerOn(first.id); } }
    if (e.key === 'Escape') { searchInput.value = ''; updateMatches(); draw(); canvas.focus(); }
  });
  root.querySelector('.graph-zoom').addEventListener('click', (e) => {
    const z = e.target.closest('[data-z]')?.dataset.z;
    if (z === 'fit') fit(true);
    else if (z) zoomAt(z === 'in' ? 1.4 : 1 / 1.4, canvas.clientWidth / 2, canvas.clientHeight / 2);
  });
  focusBar.addEventListener('click', (e) => {
    const d = e.target.closest('[data-depth]');
    if (d) { opts.depth = Number(d.dataset.depth); saveOptions(); rebuild(); return; }
    if (e.target.closest('[data-unfocus]')) setFocus(null);
  });
  details.addEventListener('click', onDetailsClick);
  statsEl.addEventListener('click', onStatsClick);
  initCanvasEvents();
  new ResizeObserver(() => { if (open) { sizeCanvas(); draw(); } }).observe(root.querySelector('.graph-main'));
  window.addEventListener('keydown', onKey);
}

function paintControls() {
  for (const el of root.querySelectorAll('[data-opt]')) {
    const v = opts[el.dataset.opt];
    if (el.type === 'checkbox') el.checked = !!v; else el.value = v;
  }
  for (const el of root.querySelectorAll('[data-edge]')) el.checked = !!opts.edges[el.dataset.edge];
  root.querySelector('[data-opt="minCited"]').disabled = !opts.works;
  const sel = root.querySelector('.graph-folder');
  sel.innerHTML = `<option value="">Whole library</option>` + folderTree().map((f) =>
    `<option value="${f.id}">${' '.repeat(f.depth)}${esc(f.name)}</option>`).join('');
  sel.value = opts.folder != null && folderTree().some((f) => f.id === opts.folder) ? String(opts.folder) : '';
  if (!sel.value) opts.folder = null;
}

function onControl(e) {
  const el = e.target;
  if (el.dataset.opt) opts[el.dataset.opt] = el.type === 'checkbox' ? el.checked : Math.max(1, Number(el.value) || 1);
  if (el.dataset.edge) opts.edges[el.dataset.edge] = el.checked;
  if (el.classList.contains('graph-folder')) opts.folder = el.value ? Number(el.value) : null;
  root.querySelector('[data-opt="minCited"]').disabled = !opts.works;
  saveOptions();
  rebuild();
}

/* ----------------------------------------------------------- open / close */
/** Show the graph (optionally around one node, e.g. "item:<id>" or "note:<path>"). */
export async function openGraph(focus = undefined, { push = true } = {}) {
  open = true;
  document.body.dataset.workspace = 'graph';
  root.hidden = false;
  emit('workspace');
  if (focus !== undefined) opts.focus = focus;
  paintControls();
  sizeCanvas();
  await load();
  if (push) navPush({ ws: 'graph', focus: opts.focus || null });
  canvas.focus();
}

export function closeGraph({ push = true } = {}) {
  if (!open) return;
  open = false;
  cancelAnimationFrame(raf);
  raf = 0;
  if (document.body.dataset.workspace === 'graph') delete document.body.dataset.workspace;
  root.hidden = true;
  hideTip();
  emit('workspace');
  if (push) {
    emit('focus-list');
    navPush({ ws: 'library', view: state.view, sel: [...state.selection] });
  }
}

async function load() {
  try {
    data = await api.graph();
  } catch (e) { toastError(e); return; }
  byId = new Map(data.nodes.map((n) => [n.id, n]));
  if (opts.focus && !byId.has(opts.focus)) {
    toast('That item or note is no longer in the graph.');
    opts.focus = null;
  }
  if (selected && !byId.has(selected)) selected = null;
  if (opts.focus) selected = opts.focus;
  rebuild();
}

/** Recompute what is shown and restart the layout from the current positions. */
function rebuild() {
  if (!data) return;
  if (layout) positions = new Map([...positions, ...layout.positions()]);
  opts.folders = opts.folder != null ? new Set(folderDescendants(opts.folder)) : null;
  view = buildView(data, opts);
  const prev = new Map(view.nodes.filter((n) => positions.has(n.id)).map((n) => [n.id, positions.get(n.id)]));
  layout = new ForceLayout(view.nodes, view.edges, prev);
  userMoved = prev.size > view.nodes.length * 0.8 && userMoved;
  // A head start, so the first picture is already roughly in shape.
  const t0 = performance.now();
  while (!layout.done && performance.now() - t0 < 60) layout.step();
  if (!userMoved) fit(false);
  const shownIds = new Set(view.nodes.map((n) => n.id));
  if (hover && !shownIds.has(hover)) hover = null;
  updateNeighbours();
  updateMatches();
  paintFocusBar();
  paintStats();
  paintDetails();
  paintEmpty();
  animate();
}

/* --------------------------------------------------------------- rendering */
function sizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, hgt = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(hgt * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(hgt * dpr);
  }
}

function readColors() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name, fallback) => cs.getPropertyValue(name).trim() || fallback;
  colors = {
    paper: v('--accent', '#2f6fed'), work: v('--text-3', '#8a93a3'), note: v('--graph-note', '#8e5cd9'),
    edge: v('--border-strong', '#cfd4dc'), text: v('--text', '#1c2330'), text2: v('--text-2', '#5b6576'),
    bg: v('--bg', '#fff'), mark: v('--warning', '#b7791f'), link: v('--success', '#1f9d55'),
  };
}

function animate() {
  if (raf || !open) return;
  const frame = () => {
    raf = 0;
    if (!open) return;
    const t0 = performance.now();
    if (!layout.done) {
      do layout.step(); while (!layout.done && performance.now() - t0 < 10);
      if (!userMoved && layout.n < 400) fit(false);
    }
    draw();
    if (!layout.done) raf = requestAnimationFrame(frame);
  };
  raf = requestAnimationFrame(frame);
}

function draw() {
  if (!open || !layout) return;
  readColors();
  sizeCanvas();
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  ctx.setTransform(dpr * cam.k, 0, 0, dpr * cam.k, dpr * cam.x, dpr * cam.y);

  const focusId = hover || selected;
  const dimming = !!focusId || matches.size > 0;
  const lit = (id) => (focusId ? neighbours.has(id) : matches.size ? matches.has(id) : true);

  // edges
  ctx.lineWidth = 1 / cam.k;
  for (const e of view.edges) {
    const s = layout.position(e.source), t = layout.position(e.target);
    if (!s || !t) continue;
    const on = focusId ? (e.source === focusId || e.target === focusId) : !matches.size;
    ctx.globalAlpha = on ? (focusId ? 0.9 : 0.55) : 0.08;
    ctx.strokeStyle = e.kind === 'cites' ? colors.edge : e.kind === 'link' ? colors.link : colors.note;
    ctx.setLineDash(e.kind === 'about' ? [4 / cam.k, 3 / cam.k] : []);
    ctx.beginPath();
    ctx.moveTo(s.x, s.y);
    ctx.lineTo(t.x, t.y);
    ctx.stroke();
    if (cam.k > 1.1 && on) arrow(s, t, radius(byId.get(e.target), view.degree.get(e.target)));
  }
  ctx.setLineDash([]);

  // nodes
  for (const n of view.nodes) {
    const p = layout.position(n.id);
    const r = radius(n, view.degree.get(n.id));
    ctx.globalAlpha = dimming && !lit(n.id) ? 0.15 : 1;
    shape(n, p, r);
    if (n.id === selected || matches.has(n.id)) {
      ctx.globalAlpha = 1;
      ctx.lineWidth = 2 / cam.k;
      ctx.strokeStyle = n.id === selected ? colors.text : colors.mark;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r + 3 / cam.k, 0, 2 * Math.PI);
      ctx.stroke();
    }
  }

  // labels (screen-sized text)
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.font = `11px ${getComputedStyle(document.body).fontFamily}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (const n of view.nodes) {
    const r = radius(n, view.degree.get(n.id));
    const important = n.id === selected || n.id === hover || matches.has(n.id) || (focusId && neighbours.has(n.id));
    if (!important && r * cam.k < 7 && cam.k < 2.2) continue;
    if (dimming && !lit(n.id) && !important) continue;
    const p = layout.position(n.id);
    const sx = p.x * cam.k + cam.x, sy = (p.y + r) * cam.k + cam.y + 3;
    if (sx < -100 || sy < -20 || sx > canvas.clientWidth + 100 || sy > canvas.clientHeight + 20) continue;
    let text = label(n);
    if (text.length > 42) text = `${text.slice(0, 40)}…`;
    ctx.globalAlpha = 1;
    ctx.lineWidth = 3;
    ctx.strokeStyle = colors.bg;
    ctx.strokeText(text, sx, sy);
    ctx.fillStyle = important ? colors.text : colors.text2;
    ctx.fillText(text, sx, sy);
  }
  ctx.globalAlpha = 1;
}

function shape(n, p, r) {
  ctx.beginPath();
  if (n.kind === 'note') {
    ctx.moveTo(p.x, p.y - r * 1.15); ctx.lineTo(p.x + r * 1.15, p.y); ctx.lineTo(p.x, p.y + r * 1.15); ctx.lineTo(p.x - r * 1.15, p.y);
    ctx.closePath();
    ctx.fillStyle = colors.note;
    ctx.fill();
  } else if (n.kind === 'work') {
    ctx.arc(p.x, p.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = colors.bg;
    ctx.fill();
    ctx.lineWidth = 1.5 / cam.k;
    ctx.strokeStyle = colors.work;
    ctx.stroke();
  } else {
    ctx.arc(p.x, p.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = colors.paper;
    ctx.fill();
  }
}

function arrow(s, t, r) {
  const dx = t.x - s.x, dy = t.y - s.y, l = Math.hypot(dx, dy);
  if (l < r * 2) return;
  const ux = dx / l, uy = dy / l, size = 5 / cam.k;
  const ex = t.x - ux * (r + 1.5 / cam.k), ey = t.y - uy * (r + 1.5 / cam.k);
  ctx.beginPath();
  ctx.moveTo(ex, ey);
  ctx.lineTo(ex - ux * size - uy * size * 0.55, ey - uy * size + ux * size * 0.55);
  ctx.lineTo(ex - ux * size + uy * size * 0.55, ey - uy * size - ux * size * 0.55);
  ctx.closePath();
  ctx.fillStyle = ctx.strokeStyle;
  ctx.fill();
}

/* ---------------------------------------------------------------- camera */
function fit(animateIt) {
  if (!layout || !layout.n) return;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i < layout.n; i++) {
    x0 = Math.min(x0, layout.x[i]); x1 = Math.max(x1, layout.x[i]);
    y0 = Math.min(y0, layout.y[i]); y1 = Math.max(y1, layout.y[i]);
  }
  const w = canvas.clientWidth || 800, hgt = canvas.clientHeight || 600, pad = 60;
  const k = Math.min(1.6, Math.max(0.05, Math.min((w - 2 * pad) / Math.max(x1 - x0, 1), (hgt - 2 * pad) / Math.max(y1 - y0, 1))));
  cam = { k, x: w / 2 - ((x0 + x1) / 2) * k, y: hgt / 2 - ((y0 + y1) / 2) * k };
  if (animateIt) { userMoved = false; draw(); }
}

function zoomAt(f, sx, sy) {
  const k = Math.min(12, Math.max(0.03, cam.k * f));
  cam.x = sx - ((sx - cam.x) * k) / cam.k;
  cam.y = sy - ((sy - cam.y) * k) / cam.k;
  cam.k = k;
  userMoved = true;
  draw();
}

function centerOn(id) {
  const p = layout?.position(id);
  if (!p) return;
  cam.x = canvas.clientWidth / 2 - p.x * cam.k;
  cam.y = canvas.clientHeight / 2 - p.y * cam.k;
  userMoved = true;
  draw();
}

/* ----------------------------------------------------------- interaction */
function nodeAt(sx, sy) {
  if (!layout) return null;
  const wx = (sx - cam.x) / cam.k, wy = (sy - cam.y) / cam.k;
  let best = null, bestD = Infinity;
  for (const n of view.nodes) {
    const p = layout.position(n.id);
    const r = radius(n, view.degree.get(n.id)) + 4 / cam.k;
    const d = (p.x - wx) ** 2 + (p.y - wy) ** 2;
    if (d <= r * r && d < bestD) { best = n.id; bestD = d; }
  }
  return best;
}

function setHover(id) {
  if (id === hover) return;
  hover = id;
  updateNeighbours();
  canvas.style.cursor = id ? 'pointer' : '';
  draw();
}

function updateNeighbours() {
  const id = hover || selected;
  neighbours = new Set(id ? [id] : []);
  if (id) for (const e of view.edges) {
    if (e.source === id) neighbours.add(e.target);
    else if (e.target === id) neighbours.add(e.source);
  }
}

function select(id) {
  selected = id;
  updateNeighbours();
  paintDetails();
  draw();
}

function initCanvasEvents() {
  let drag = null;   // {kind: 'pan' | 'node', id, sx, sy, moved}
  const local = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  canvas.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const [sx, sy] = local(e);
    const id = nodeAt(sx, sy);
    drag = { kind: id ? 'node' : 'pan', id, sx, sy, cx: cam.x, cy: cam.y, moved: false };
    canvas.setPointerCapture(e.pointerId);
    hideTip();
  });
  canvas.addEventListener('pointermove', (e) => {
    const [sx, sy] = local(e);
    if (!drag) {
      const id = nodeAt(sx, sy);
      setHover(id);
      if (id) showTip(id, sx, sy); else hideTip();
      return;
    }
    if (!drag.moved && Math.hypot(sx - drag.sx, sy - drag.sy) < 3) return;
    drag.moved = true;
    if (drag.kind === 'pan') {
      cam.x = drag.cx + sx - drag.sx;
      cam.y = drag.cy + sy - drag.sy;
      userMoved = true;
      draw();
    } else {
      layout.pin(drag.id, { x: (sx - cam.x) / cam.k, y: (sy - cam.y) / cam.k });
      layout.reheat(0.25);
      userMoved = true;
      animate();
    }
  });
  canvas.addEventListener('pointerup', () => {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (d.kind === 'node' && d.moved) { layout.pin(d.id, null); return; }
    if (!d.moved) select(d.kind === 'node' ? d.id : null);
  });
  canvas.addEventListener('pointerleave', () => { if (!drag) { setHover(null); hideTip(); } });
  canvas.addEventListener('dblclick', (e) => {
    const [sx, sy] = local(e);
    const id = nodeAt(sx, sy);
    if (id) openNode(byId.get(id));
  });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const [sx, sy] = local(e);
    zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0025)), sx, sy);
  }, { passive: false });
}

function onKey(e) {
  if (!open || e.target.closest?.('input, textarea, select, [contenteditable="true"]')) return;
  if (e.key === 'Escape') { if (selected) select(null); else if (opts.focus) setFocus(null); }
  else if (e.key === 'f' && !e.metaKey && !e.ctrlKey) fit(true);
  else if (e.key === 'Enter' && selected) openNode(byId.get(selected));
  else if (e.key === '/' || ((e.metaKey || e.ctrlKey) && e.key === 'f')) { e.preventDefault(); searchInput.focus(); searchInput.select(); }
}

function showTip(id, sx, sy) {
  const n = byId.get(id);
  tip.innerHTML = `<div class="t">${esc(n.title || label(n))}</div>
    <div class="m">${esc(kindLabel(n))}${n.kind !== 'note' && n.authors ? ` · ${esc(shortAuthors(n.authors))}` : ''}${n.year ? ` · ${n.year}` : ''}</div>`;
  tip.hidden = false;
  const w = tip.offsetWidth, hgt = tip.offsetHeight;
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  tip.style.left = `${Math.min(sx + 14, cw - w - 8)}px`;
  tip.style.top = `${sy + 14 + hgt > ch ? sy - hgt - 10 : sy + 14}px`;
}

function hideTip() { if (tip) tip.hidden = true; }

function updateMatches() {
  matches = new Set(matchNodes(view.nodes, searchInput?.value || '').map((n) => n.id));
}

/* ------------------------------------------------------------------ focus */
function setFocus(id) {
  opts.focus = id;
  if (id) selected = id;
  rebuild();
  fit(true);
  navPush({ ws: 'graph', focus: id || null });
}

function paintFocusBar() {
  const n = opts.focus && byId.get(opts.focus);
  focusBar.hidden = !n;
  if (!n) return;
  focusBar.innerHTML = `<span>Around <b>${esc(label(n))}</b></span>
    <span class="segmented">${[1, 2, 3].map((d) => `<button data-depth="${d}" class="${opts.depth === d ? 'active' : ''}" title="Show nodes up to ${d} step${d > 1 ? 's' : ''} away">${d}</button>`).join('')}</span>
    <button class="btn btn-sm" data-unfocus>Show everything</button>`;
}

/* ---------------------------------------------------------------- details */
function kindLabel(n) {
  return n.kind === 'paper' ? 'In your library' : n.kind === 'work' ? 'Cited work (not in your library)' : n.note_type === 'paper' || n.paper ? 'Paper note' : 'Note';
}

function paintDetails() {
  const n = selected && byId.get(selected);
  if (!n) {
    details.innerHTML = `<div class="graph-help">
      <p><b>Click</b> a node to see what it connects to, <b>double-click</b> to open it.</p>
      <p>Drag to pan, scroll to zoom, drag a node to move it. <kbd>F</kbd> fits, <kbd>/</kbd> finds.</p>
      <p>Right-click an item in the library or a note and choose <b>Show in Graph</b> to see its neighbourhood.</p></div>`;
    return;
  }
  const r = relations(data, n.id);
  const ident = n.doi ? `doi:${n.doi}` : n.arxiv_id ? `arXiv:${n.arxiv_id}` : '';
  const actions = [];
  if (n.kind === 'paper') {
    actions.push(`<button class="btn btn-sm" data-act="show-item">${icon('library', 'icon-sm')} Show in Library</button>`);
    if (n.has_pdf) actions.push(`<button class="btn btn-sm" data-act="pdf">${icon('pdf', 'icon-sm')} Open PDF</button>`);
    if (r.notes.length) actions.push(`<button class="btn btn-sm" data-act="note" data-path="${esc(r.notes[0].path)}">${icon('edit', 'icon-sm')} Paper Note</button>`);
  } else if (n.kind === 'note') {
    actions.push(`<button class="btn btn-sm" data-act="note" data-path="${esc(n.path)}">${icon('edit', 'icon-sm')} Open Note</button>`);
  } else if (ident) {
    actions.push(`<button class="btn btn-sm" data-act="add" data-ident="${esc(n.doi || n.arxiv_id)}">${icon('plus', 'icon-sm')} Add to Library</button>`);
  }
  if (opts.focus !== n.id) actions.push(`<button class="btn btn-sm" data-act="focus">${icon('find', 'icon-sm')} Focus</button>`);
  const list = (title, nodes) => !nodes.length ? '' : `<section><h4>${title} <span class="muted">${nodes.length}</span></h4><ul>${
    nodes.slice(0, 200).map((m) => `<li><button data-go="${esc(m.id)}" class="${m.kind}" title="${esc(m.title || '')}">
      <span class="dot ${m.kind}"></span><span class="lbl">${esc(m.kind === 'note' ? m.title : `${label(m)} — ${m.title || ''}`)}</span></button></li>`).join('')}</ul></section>`;
  const citesTitle = n.kind === 'note' ? 'Cites' : 'References';
  details.innerHTML = `
    <div class="graph-node-head"><span class="dot ${n.kind}"></span><span class="muted">${esc(kindLabel(n))}</span></div>
    <h3>${esc(n.title || label(n))}</h3>
    ${n.kind !== 'note' && n.authors ? `<div class="muted">${esc(shortAuthors(n.authors))}${n.year ? ` · ${n.year}` : ''}</div>` : ''}
    ${n.citation_key ? `<div class="mono muted">@${esc(n.citation_key)}</div>` : ''}
    ${n.kind === 'note' ? `<div class="mono muted">${esc(n.path)}</div>` : ''}
    ${ident && n.kind === 'work' ? `<div class="mono muted">${esc(ident)}</div>` : ''}
    <div class="graph-actions">${actions.join('')}</div>
    ${list(citesTitle, r.cites)}
    ${list('Cited by', r.citedBy)}
    ${list('Paper notes', r.notes)}
    ${list('Cited in notes', r.citedInNotes)}
    ${list('About', r.about)}
    ${list('Links to', r.linksTo)}
    ${list('Linked from', r.linkedFrom)}
    ${n.kind === 'paper' && !r.cites.length ? `<p class="muted small">No reference list yet. <button class="btn-link" data-act="fetch">Fetch references</button></p>` : ''}`;
}

async function onDetailsClick(e) {
  const go = e.target.closest('[data-go]');
  if (go) {
    const id = go.dataset.go;
    if (!view.nodes.some((n) => n.id === id)) { setFocus(id); return; }   // hidden by the filters: show its neighbourhood
    select(id);
    centerOn(id);
    return;
  }
  const b = e.target.closest('[data-act]');
  if (!b) return;
  const n = byId.get(selected);
  if (!n) return;
  const act = b.dataset.act;
  if (act === 'focus') setFocus(n.id);
  else if (act === 'show-item') { closeGraph(); emit('show-item', n.item_id); }
  else if (act === 'pdf') A.openPdf(n.item_id);
  else if (act === 'note') { closeGraph({ push: false }); emit('open-note', { path: b.dataset.path }); }
  else if (act === 'fetch') {
    try {
      const v = await api.fetchReferences(n.item_id);
      toast(v.fetch?.count ? `Found ${plural(v.fetch.count, 'reference')}.` : 'No reference list found for this item.');
      await load();
    } catch (err) { toastError(err); }
  } else if (act === 'add') {
    b.disabled = true;
    try {
      await api.fromIdentifier(b.dataset.ident, false, null);
      toast('Added to your library.', { type: 'success' });
      await A.loadItems();
      await load();
    } catch (err) {
      if (err.status === 409) toast('Already in your library.'); else toastError(err);
      b.disabled = false;
    }
  }
}

function openNode(n) {
  if (!n) return;
  if (n.kind === 'paper') { closeGraph(); emit('show-item', n.item_id); }
  else if (n.kind === 'note') { closeGraph({ push: false }); emit('open-note', { path: n.path }); }
  else setFocus(n.id);
}

/* ------------------------------------------------------------ stats, empty */
function paintStats() {
  const s = data.stats;
  const done = new Set(data.fetched || []);
  const missing = data.nodes.filter((n) => n.kind === 'paper' && (n.doi || n.arxiv_id) && !done.has(n.item_id)).length;
  statsEl.innerHTML = `<h4>Showing</h4>
    <div>${plural(view.nodes.length, 'node')} · ${plural(view.edges.length, 'link')}</div>
    <div class="muted small">Library: ${plural(s.papers, 'paper')}, ${plural(s.notes, 'note')}; reference lists for ${s.items_with_references}.</div>
    ${missing > 0 ? `<button class="btn btn-sm" data-act="fetch-all" title="Fetch the reference lists of the papers that have a DOI or arXiv ID but no list yet">${icon('link', 'icon-sm')} Fetch missing references (${missing})</button>` : ''}`;
}

async function onStatsClick(e) {
  if (!e.target.closest('[data-act="fetch-all"]')) return;
  const done = new Set(data.fetched || []);
  const ids = data.nodes.filter((n) => n.kind === 'paper' && (n.doi || n.arxiv_id) && !done.has(n.item_id)).map((n) => n.item_id);
  await fetchReferencesBulk(ids);
  await load();
}

function paintEmpty() {
  const el = root.querySelector('.graph-empty');
  const empty = !view.nodes.length;
  el.hidden = !empty;
  if (!empty) return;
  const noRefs = !data.stats.items_with_references;
  el.innerHTML = `${icon('link')}<h3>Nothing to show</h3><p>${noRefs
    ? 'No reference lists have been fetched yet, and no notes cite your papers. Fetch references from the item menu (or with the button on the left), or cite papers in notes with [@key].'
    : 'Nothing matches these options. Try showing more kinds of nodes, a lower “cited by” number, or the whole library.'}</p>`;
}

window.zcGraph = {   // for tests
  get view() { return view; }, get selected() { return selected; }, get opts() { return opts; },
  screenOf(id) { const p = layout?.position(id); return p && { x: p.x * cam.k + cam.x, y: p.y * cam.k + cam.y }; },
  settle() { while (layout && !layout.done) layout.step(); draw(); },
};
