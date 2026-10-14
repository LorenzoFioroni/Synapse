// @ts-nocheck
// PDF viewer → notes: "Quote to note" for selected text and "Snapshot to note" for a region.
//
// Loaded by viewer.html as a separate module so the viewer's own code stays untouched.
// A quote or snapshot goes either into a note open in a Notes tab (inserted at the cursor,
// via BroadcastChannel 'zc-notes' — see notesView.js) or into the paper note of this PDF's item
// (appended on the server, created if missing).

import { writeRegion } from '../views/notes/citeTarget.js';
import { findQuote, parseRect, quoteBoxes } from './locate.js';

const params = new URLSearchParams(location.search);
const paperId = params.get('paper_id');
const channel = 'BroadcastChannel' in window ? new BroadcastChannel('zc-notes') : null;
let item = null;   // {id, citation_key, title}

// With several PDFs on the item, the citation names this one (`att:<id>`, see backend/notes/parser.py);
// with one PDF it stays as short as before.
function attQualifier() {
  const id = Number(params.get('att'));
  return id && item?.attachments?.length > 1 ? `, att:${id}` : '';
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* -------------------------------------------------------------------- styles */
const css = document.createElement('style');
css.textContent = `
  .zc-pill { position: fixed; z-index: 3000; display: flex; gap: 4px; padding: 3px; border-radius: 8px; background: var(--bg-elev, #fff);
    border: 1px solid var(--border, #ddd); box-shadow: 0 6px 20px rgba(0,0,0,.18); font: 12px -apple-system, system-ui, sans-serif; }
  .zc-pill button { border: none; background: none; padding: 5px 9px; border-radius: 6px; color: var(--text, #222); cursor: pointer; font: inherit; white-space: nowrap; }
  .zc-pill button:hover { background: var(--bg-hover, #eef); }
  .zc-menu { flex-direction: column; min-width: 220px; }
  .zc-menu .zc-head { padding: 4px 9px 2px; color: var(--text-3, #888); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
  .zc-menu button { text-align: left; }
  .zc-toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); z-index: 3001; background: #1c2330; color: #fff;
    padding: 8px 14px; border-radius: 8px; font: 13px -apple-system, system-ui, sans-serif; box-shadow: 0 6px 20px rgba(0,0,0,.25); }
  .zc-snap-on .page-wrapper { cursor: crosshair; }
  .zc-snap-layer { position: absolute; inset: 0; z-index: 50; touch-action: none; }
  .zc-snap-rect { position: absolute; border: 2px solid #2f6fed; background: rgba(47,111,237,.12); pointer-events: none; }
  #zc-snap-btn.on { background: var(--accent-soft, #e4ecfd); color: var(--accent, #2f6fed); }
  /* a cited spot, flashed for a moment when the viewer opens on it (not an annotation) */
  .zc-flash { position: absolute; z-index: 40; pointer-events: none; border-radius: 3px;
    background: rgba(255, 196, 0, .38); box-shadow: 0 0 0 2px rgba(255, 166, 0, .85), 0 0 18px 4px rgba(255, 180, 0, .45);
    animation: zc-flash 2.8s ease-out forwards; }
  @keyframes zc-flash { 0% { opacity: 0; } 8% { opacity: 1; } 22% { opacity: .35; } 36% { opacity: 1; } 50% { opacity: .35; }
    64% { opacity: 1; } 100% { opacity: 0; } }
  @media (prefers-reduced-motion: reduce) { .zc-flash { animation: zc-fade 2.8s ease-out forwards; } }
  @keyframes zc-fade { 0%, 60% { opacity: 1; } 100% { opacity: 0; } }
`;
document.head.appendChild(css);

function toast(msg) {
  const t = document.createElement('div');
  t.className = 'zc-toast';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

/* --------------------------------------------------------------- destinations */
function askOpenNotes(timeout = 250) {
  if (!channel) return Promise.resolve([]);
  const req = Math.random().toString(36).slice(2);
  const found = [];
  return new Promise((resolve) => {
    const onMsg = (e) => { if (e.data?.type === 'here' && e.data.req === req) found.push(e.data); };
    channel.addEventListener('message', onMsg);
    channel.postMessage({ type: 'who', req });
    setTimeout(() => { channel.removeEventListener('message', onMsg); resolve(found); }, timeout);
  });
}

function sendToTab(tab, markdown) {
  const req = Math.random().toString(36).slice(2);
  return new Promise((resolve) => {
    const onMsg = (e) => {
      if (e.data?.type === 'inserted' && e.data.req === req) { channel.removeEventListener('message', onMsg); resolve(true); }
    };
    channel.addEventListener('message', onMsg);
    channel.postMessage({ type: 'insert', req, tab, markdown });
    setTimeout(() => { channel.removeEventListener('message', onMsg); resolve(false); }, 1500);
  });
}

async function appendToPaperNote(markdown) {
  const r = await fetch('/api/notes/append', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_id: item.id, markdown }) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
  return d;
}

let menu = null;
function closeMenu() { menu?.remove(); menu = null; }

/** Ask where the block should go (open notes, or the paper note) and send it there. */
async function deliver(markdown, at) {
  const open = await askOpenNotes();
  const go = async (dest) => {
    closeMenu();
    try {
      if (dest) {
        if (await sendToTab(dest.tab, markdown)) { toast(`Added to “${dest.title}”`); return; }
        toast('The note window did not answer — adding to the paper note instead.');
      }
      const res = await appendToPaperNote(markdown);
      toast(res.created ? `Created the paper note ${res.path}` : `Added to the paper note`);
    } catch (e) { toast(e.message); }
  };
  if (!open.length) return go(null);
  closeMenu();
  menu = document.createElement('div');
  menu.className = 'zc-pill zc-menu';
  menu.innerHTML = `<div class="zc-head">Add to</div>${open.map((o, i) => `<button data-i="${i}">“${esc(o.title)}” (open note)</button>`).join('')}
    <button data-i="-1">Paper note of @${esc(item.citation_key)}</button>`;
  menu.style.left = `${Math.min(at.x, innerWidth - 260)}px`;
  menu.style.top = `${Math.min(at.y, innerHeight - 160)}px`;
  menu.addEventListener('pointerdown', (e) => e.preventDefault());
  menu.addEventListener('click', (e) => {
    const b = e.target.closest('[data-i]');
    if (b) go(Number(b.dataset.i) >= 0 ? open[Number(b.dataset.i)] : null);
  });
  document.body.appendChild(menu);
}

/* ------------------------------------------------------------------- quotes */
let pill = null;
let lastPointer = 'mouse';
document.addEventListener('pointerdown', (e) => { lastPointer = e.pointerType || 'mouse'; }, true);
function hidePill() { pill?.remove(); pill = null; }

/** Text as one paragraph: line breaks become spaces, words hyphenated across lines are joined. */
export function cleanQuote(text) {
  return text.replace(/(\w)-\s*\n\s*(\w)/g, '$1$2').replace(/\s+/g, ' ').trim();
}

function onSelectionEnd() {
  hidePill();
  const sel = window.getSelection();
  const text = sel && !sel.isCollapsed ? sel.toString() : '';
  if (!text.trim() || !item) return;
  const node = sel.anchorNode?.nodeType === 1 ? sel.anchorNode : sel.anchorNode?.parentElement;
  const wrapper = node?.closest?.('.page-wrapper');
  if (!wrapper || !node.closest('.textLayer')) return;
  const page = Number(wrapper.dataset.pageNumber) || 1;
  const rect = sel.getRangeAt(0).getBoundingClientRect();
  pill = document.createElement('div');
  pill.className = 'zc-pill';
  pill.innerHTML = item.citation_key
    ? '<button data-q>❝ Quote to note</button>'
    : '<button disabled title="Give the item a citation key first">No citation key</button>';
  pill.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - 160))}px`;
  pill.style.top = `${rect.bottom + 8 > innerHeight - 40 ? rect.top - 40 : rect.bottom + 8}px`;
  pill.addEventListener('pointerdown', (e) => e.preventDefault());   // keep the selection
  pill.querySelector('[data-q]')?.addEventListener('click', () => {
    const md = `> ${cleanQuote(text)} [@${item.citation_key}, p. ${page}${attQualifier()}]`;
    const at = { x: parseFloat(pill.style.left), y: parseFloat(pill.style.top) };
    hidePill();
    deliver(md, at);
  });
  document.body.appendChild(pill);
}

/* ---------------------------------------------------------------- snapshots */
let snapping = false;
function setSnap(on) {
  snapping = on;
  document.body.classList.toggle('zc-snap-on', on);
  document.getElementById('zc-snap-btn')?.classList.toggle('on', on);
  document.querySelectorAll('.zc-snap-layer').forEach((l) => l.remove());
  if (!on) return;
  for (const w of document.querySelectorAll('.page-wrapper')) {
    const layer = document.createElement('div');
    layer.className = 'zc-snap-layer';
    layer.addEventListener('pointerdown', (e) => { if (e.button <= 0) startSnap(e, w, layer); });
    w.appendChild(layer);
  }
  toast('Drag a rectangle over the part of the page to copy (Esc to cancel).');
}

function startSnap(e, wrapper, layer) {
  e.preventDefault();
  layer.setPointerCapture?.(e.pointerId);   // mouse, Pencil or finger (issue #34)
  const id = e.pointerId;
  const box = layer.getBoundingClientRect();
  const x0 = e.clientX - box.left, y0 = e.clientY - box.top;
  const r = document.createElement('div');
  r.className = 'zc-snap-rect';
  layer.appendChild(r);
  const move = (ev) => {
    if (ev.pointerId !== id) return;
    const x1 = Math.max(0, Math.min(ev.clientX - box.left, box.width)), y1 = Math.max(0, Math.min(ev.clientY - box.top, box.height));
    Object.assign(r.style, { left: `${Math.min(x0, x1)}px`, top: `${Math.min(y0, y1)}px`, width: `${Math.abs(x1 - x0)}px`, height: `${Math.abs(y1 - y0)}px` });
  };
  const up = async (ev) => {
    if (ev.pointerId !== id) return;
    layer.removeEventListener('pointermove', move);
    layer.removeEventListener('pointerup', up);
    layer.removeEventListener('pointercancel', up);
    if (ev.type === 'pointercancel') { setSnap(false); return; }
    const sel = { x: parseFloat(r.style.left) || 0, y: parseFloat(r.style.top) || 0, w: parseFloat(r.style.width) || 0, h: parseFloat(r.style.height) || 0 };
    setSnap(false);
    if (sel.w < 8 || sel.h < 8) return;
    try {
      const page = Number(wrapper.dataset.pageNumber) || 1;
      // the PNG remembers where on the page it was cut from, so its citation can open right there
      const W = wrapper.clientWidth, H = wrapper.clientHeight;
      const region = { page, rect: [sel.x / W, sel.y / H, sel.w / W, sel.h / H].map((v) => +v.toFixed(4)) };
      const png = writeRegion(new Uint8Array(await (await crop(wrapper, sel)).arrayBuffer()), region);
      const blob = new Blob([png], { type: 'image/png' });
      const fd = new FormData();
      fd.append('file', blob, `${item.citation_key}-p${page}.png`);
      const res = await fetch('/api/notes/attachments', { method: 'POST', body: fd });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || 'Upload failed');
      const md = `![${item.citation_key} p. ${page}](${d.path})\n*Figure: [@${item.citation_key}, p. ${page}${attQualifier()}]*`;
      deliver(md, { x: ev.clientX, y: ev.clientY });
    } catch (err) { toast(err.message); }
  };
  layer.addEventListener('pointermove', move);
  layer.addEventListener('pointerup', up);
  layer.addEventListener('pointercancel', up);
  move(e);
}

/** PNG of a region of the page (in CSS pixels of the wrapper), at the canvas's resolution. */
function crop(wrapper, sel) {
  const canvas = wrapper.querySelector('canvas');
  const cr = canvas.getBoundingClientRect(), wr = wrapper.getBoundingClientRect();
  const sx = canvas.width / cr.width, sy = canvas.height / cr.height;
  const ox = cr.left - wr.left, oy = cr.top - wr.top;
  const out = document.createElement('canvas');
  out.width = Math.round(sel.w * sx);
  out.height = Math.round(sel.h * sy);
  out.getContext('2d').drawImage(canvas, (sel.x - ox) * sx, (sel.y - oy) * sy, sel.w * sx, sel.h * sy, 0, 0, out.width, out.height);
  return new Promise((resolve, reject) => out.toBlob((b) => (b ? resolve(b) : reject(new Error('Could not copy the region'))), 'image/png'));
}

/* ------------------------------------------------------- open at ?page=N */
// Links to a cited page (e.g. [@key, p. 7]) open the viewer with page=7, and with q=… (the
// words of a quote) or rect=x,y,w,h (a snapshot's region, as fractions of the page) when the
// citation belongs to one. Pages are created one after the other: wait for that page, scroll to
// the spot (or the top of the page) and flash it — unless the reader has already started scrolling.
export function jumpToRequestedPage() {
  const want = parseInt(params.get('page'), 10);
  const q = params.get('q'), rect = params.get('rect');
  const container = document.getElementById('pdf-container');
  if (!(want >= 1) || !container || (want === 1 && !q && !rect)) return;
  let userMoved = false;
  const stop = () => { userMoved = true; };
  container.addEventListener('wheel', stop, { passive: true, once: true });
  container.addEventListener('touchmove', stop, { passive: true, once: true });
  window.addEventListener('keydown', stop, { once: true });
  jumpTo({ page: want, q, rect }, () => userMoved);
}

/** Wait for a page's wrapper (up to 60 s, or until the reader scrolls); null if it never comes. */
function pageWrapper(container, num, userMoved) {
  const find = () => container.querySelector(`.page-wrapper[data-page-number="${num}"]`);
  if (find()) return Promise.resolve(find());
  return new Promise((resolve) => {
    const obs = new MutationObserver(() => {
      if (find() || userMoved()) { obs.disconnect(); resolve(userMoved() ? null : find()); }
    });
    obs.observe(container, { childList: true });
    setTimeout(() => { obs.disconnect(); resolve(null); }, 60000);
  });
}

/** Scroll to the top of `page`, or to the quote / region on it and flash it. */
async function jumpTo({ page, q, rect }, userMoved = () => false) {
  const container = document.getElementById('pdf-container');
  const w = container && await pageWrapper(container, page, userMoved);
  if (!w) return false;
  if (!userMoved()) w.scrollIntoView({ block: 'start' });
  if (q || rect) await spotlight(w, container, { q, rect }, userMoved);
  return true;
}

/**
 * Another citation of this PDF, clicked in a note while this tab is already open (api.js reuses
 * the tab instead of opening a new one): jump there as a new Back/Forward entry. No page = just
 * show the tab where it is.
 */
function gotoSpot({ page, q, rect } = {}) {
  page = parseInt(page, 10);
  if (!(page >= 1)) return;
  const url = new URL(location.href);
  for (const [k, v] of Object.entries({ page, q, rect })) { if (v) url.searchParams.set(k, v); else url.searchParams.delete(k); }
  const run = () => jumpTo({ page, q, rect });
  if (window.zcLinks?.jumpRecorded) window.zcLinks.jumpRecorded(run, url.href);
  else { history.replaceState(history.state, '', url.href); run(); }
}
window.zcViewerGoto = gotoSpot;
// Name the tab after its file (unless the opener already did), so a citation clicked later can
// find this tab even if it was opened with Cmd/Ctrl+click or from another view.
if (!window.name && params.get('file')) window.name = `zc-pdf:${params.get('file')}`;

/** The rectangles (page units at scale 1) of the cited spot on this page: a quote or a region. */
export async function spotRects(wrapper, { q, rect }) {
  const W = parseFloat(wrapper.dataset.baseWidth), H = parseFloat(wrapper.dataset.baseHeight);
  if (rect) { const r = parseRect(rect, W, H); return r ? [r] : []; }
  const page = wrapper.zcPage;
  if (!page || !q || !window.pdfjsLib) return [];
  const viewport = page.getViewport({ scale: 1 });
  const content = await page.getTextContent();
  const items = content.items.filter((it) => it.str).map((it) => {
    const tx = window.pdfjsLib.Util.transform(viewport.transform, it.transform);
    const h = Math.hypot(tx[2], tx[3]);
    return { str: it.str, x: tx[0] >= 0 ? tx[4] : tx[4] - it.width, y: tx[5] - h, w: it.width * viewport.scale, h: h * 1.15 };
  });
  return quoteBoxes(findQuote(items, q));
}

/** Scroll the cited spot into view and flash it for a moment (the flash is not saved). */
export async function spotlight(wrapper, container, target, userMoved = () => false) {
  const rects = await spotRects(wrapper, target);
  if (!rects.length) return false;
  const W = parseFloat(wrapper.dataset.baseWidth), H = parseFloat(wrapper.dataset.baseHeight);
  if (!userMoved()) {
    const top = Math.min(...rects.map((r) => r.y)) / H * wrapper.clientHeight;
    const cr = container.getBoundingClientRect(), wr = wrapper.getBoundingClientRect();
    container.scrollTop += wr.top - cr.top + top - container.clientHeight / 3;
  }
  wrapper.querySelectorAll('.zc-flash').forEach((f) => f.remove());
  for (const r of rects) {
    const f = document.createElement('div');
    f.className = 'zc-flash';
    const pad = 2;   // page units around the words
    Object.assign(f.style, { left: `${((r.x - pad) / W) * 100}%`, top: `${((r.y - pad) / H) * 100}%`,
      width: `${((r.w + 2 * pad) / W) * 100}%`, height: `${((r.h + 2 * pad) / H) * 100}%` });
    f.addEventListener('animationend', () => f.remove());
    wrapper.appendChild(f);
  }
  return true;
}
jumpToRequestedPage();

/* --------------------------------------------------------------------- setup */
async function init() {
  if (!paperId) return;
  try {
    const r = await fetch(`/api/items/${encodeURIComponent(paperId)}`);
    if (!r.ok) return;
    item = await r.json();
  } catch { return; }
  const tools = document.getElementById('tools-section');
  if (tools && item.citation_key) {
    const b = document.createElement('button');
    b.id = 'zc-snap-btn';
    b.className = 'icon-btn';
    b.title = 'Snapshot to note: copy a region of the page into a note';
    b.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="2" y="4" width="12" height="9" rx="1.5"/><circle cx="8" cy="8.5" r="2.4"/><path d="M5.5 4l1-1.5h3l1 1.5"/></svg>';
    b.addEventListener('click', () => setSnap(!snapping));
    tools.prepend(b);
  }
  document.getElementById('pdf-container')?.addEventListener('mouseup', () => { if (lastPointer === 'mouse') setTimeout(onSelectionEnd, 0); });
  // On a touch screen text is selected with a long press and the handles: offer the quote once the
  // selection has settled (issue #34).
  let selTimer = null;
  document.addEventListener('selectionchange', () => {
    if (lastPointer === 'mouse') return;
    clearTimeout(selTimer);
    selTimer = setTimeout(onSelectionEnd, 350);
  });
  document.addEventListener('pointerdown', (e) => {
    if (pill && !pill.contains(e.target)) hidePill();
    if (menu && !menu.contains(e.target)) closeMenu();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { setSnap(false); hidePill(); closeMenu(); } });
  window.zcViewerBridge = { ready: true, item, spotlight };   // for tests
}

init();
