// PDF viewer → notes: "Quote to note" for selected text and "Snapshot to note" for a region.
//
// Loaded by viewer.html as a separate module so the viewer's own code stays untouched.
// A quote or snapshot goes either into a note open in a Notes tab (inserted at the cursor,
// via BroadcastChannel 'zc-notes' — see notesView.js) or into the paper note of this PDF's item
// (appended on the server, created if missing).

const params = new URLSearchParams(location.search);
const paperId = params.get('paper_id');
const channel = 'BroadcastChannel' in window ? new BroadcastChannel('zc-notes') : null;
let item = null;   // {id, citation_key, title}

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
  .zc-snap-layer { position: absolute; inset: 0; z-index: 50; }
  .zc-snap-rect { position: absolute; border: 2px solid #2f6fed; background: rgba(47,111,237,.12); pointer-events: none; }
  #zc-snap-btn.on { background: var(--accent-soft, #e4ecfd); color: var(--accent, #2f6fed); }
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
  menu.addEventListener('mousedown', (e) => e.preventDefault());
  menu.addEventListener('click', (e) => {
    const b = e.target.closest('[data-i]');
    if (b) go(Number(b.dataset.i) >= 0 ? open[Number(b.dataset.i)] : null);
  });
  document.body.appendChild(menu);
}

/* ------------------------------------------------------------------- quotes */
let pill = null;
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
  pill.addEventListener('mousedown', (e) => e.preventDefault());   // keep the selection
  pill.querySelector('[data-q]')?.addEventListener('click', () => {
    const md = `> ${cleanQuote(text)} [@${item.citation_key}, p. ${page}]`;
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
    layer.addEventListener('mousedown', (e) => startSnap(e, w, layer));
    w.appendChild(layer);
  }
  toast('Drag a rectangle over the part of the page to copy (Esc to cancel).');
}

function startSnap(e, wrapper, layer) {
  e.preventDefault();
  const box = layer.getBoundingClientRect();
  const x0 = e.clientX - box.left, y0 = e.clientY - box.top;
  const r = document.createElement('div');
  r.className = 'zc-snap-rect';
  layer.appendChild(r);
  const move = (ev) => {
    const x1 = Math.max(0, Math.min(ev.clientX - box.left, box.width)), y1 = Math.max(0, Math.min(ev.clientY - box.top, box.height));
    Object.assign(r.style, { left: `${Math.min(x0, x1)}px`, top: `${Math.min(y0, y1)}px`, width: `${Math.abs(x1 - x0)}px`, height: `${Math.abs(y1 - y0)}px` });
  };
  const up = async (ev) => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
    const sel = { x: parseFloat(r.style.left) || 0, y: parseFloat(r.style.top) || 0, w: parseFloat(r.style.width) || 0, h: parseFloat(r.style.height) || 0 };
    setSnap(false);
    if (sel.w < 8 || sel.h < 8) return;
    try {
      const blob = await crop(wrapper, sel);
      const page = Number(wrapper.dataset.pageNumber) || 1;
      const fd = new FormData();
      fd.append('file', blob, `${item.citation_key}-p${page}.png`);
      const res = await fetch('/api/notes/attachments', { method: 'POST', body: fd });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || 'Upload failed');
      const md = `![${item.citation_key} p. ${page}](${d.path})\n*Figure: [@${item.citation_key}, p. ${page}]*`;
      deliver(md, { x: ev.clientX, y: ev.clientY });
    } catch (err) { toast(err.message); }
  };
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
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
  document.getElementById('pdf-container')?.addEventListener('mouseup', () => setTimeout(onSelectionEnd, 0));
  document.addEventListener('mousedown', (e) => {
    if (pill && !pill.contains(e.target)) hidePill();
    if (menu && !menu.contains(e.target)) closeMenu();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { setSnap(false); hidePill(); closeMenu(); } });
  window.zcViewerBridge = { ready: true, item };   // for tests
}

init();
