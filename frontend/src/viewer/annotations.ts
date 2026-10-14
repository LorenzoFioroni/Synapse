// PDF annotations: every highlight (of text or of an area), underline, box, drawing and text box
// is its own record, saved on its own through /api/attachments/<id>/annotations — so two tabs on
// one PDF never overwrite each other, and the app (and Claude) can read what was highlighted.
//
// Positions are fractions of the page as displayed (0–1, origin top left), sizes are PDF points at
// 100 % zoom; see backend/library/annotations.py. Each page gets an SVG layer whose viewBox is the page in
// points, so drawings scale with the zoom by themselves. Text boxes are HTML in a layer of their own
// above it (Safari misplaces HTML inside an SVG foreignObject), sized through --zc-scale.
//
// The viewer (viewer.html) owns the toolbar: the current tool is document.body.dataset.zcTool,
// the colour #tool-color and the size #tool-size. It fires 'zc:pagecreated' for each page and
// 'zc:toolchange' when a tool is chosen.
//
// The code is in parts: annotations/model.ts (the records, drawing them, saving each change,
// undo), annotations/input.ts (the tools: pointer, Pencil and touch input, and the controls of a
// selected annotation), annotations/textMarks.ts (highlighting and underlining text, snapped to
// words, one rect per line, with the geometry in annotations/lines.ts), annotations/pointer.ts
// (mouse, Pencil or finger), annotations/prefs.ts (each tool's colour and size, and the Settings)
// and this module (the layers on each page, loading, and picking up changes made elsewhere).
import { setupLayer } from './annotations/input.js';
import {
  type Annotation, api, attId, byId, clone, draw, el, layers, modeClass, notify, pageSize, redoStack, resolveAttachment,
  textLayers, undo, undoStack,
} from './annotations/model.js';

/* ------------------------------------------------------------------ pages and loading */
function addLayer(wrapper: HTMLElement): void {
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

async function load(): Promise<void> {
  if (!(await resolveAttachment())) return;
  try {
    const res = await api('GET', `/api/attachments/${attId}/annotations`);
    byId.clear();
    for (const a of res.annotations) byId.set(a.id, a);
  } catch (e) { notify(`Annotations could not be loaded: ${(e as Error).message}`); }
  clearDrawn();
  for (const rec of byId.values()) draw(rec);
}

function clearDrawn(): void {
  document.querySelectorAll('.anno-layer .anno:not(.anno-draft)').forEach((n) => n.remove());
}

/** Back in this tab: pick up what another tab changed on the same PDF. */
async function refresh(): Promise<void> {
  if (!attId || (document.activeElement as HTMLElement | null)?.isContentEditable) return;
  try {
    const res = await api('GET', `/api/attachments/${attId}/annotations`);
    const sig = (list: Annotation[]) => list.map((a) => `${a.id}:${a.modified}`).sort().join('|');
    if (sig(res.annotations) === sig([...byId.values()])) return;
    byId.clear();
    for (const a of res.annotations) byId.set(a.id, a);
    clearDrawn();
    for (const rec of byId.values()) draw(rec);
  } catch { /* offline: keep what is shown */ }
}
window.addEventListener('focus', refresh);

/* Annotations made on another device (or in another tab) appear without a reload. */
let feed: { seq?: number; epoch?: string } = {};
async function pollChanges(): Promise<void> {
  if (document.hidden || !attId) return;
  try {
    const q = new URLSearchParams(feed.seq === undefined ? {} : { since: String(feed.seq), epoch: feed.epoch ?? '' });
    const res = await api('GET', `/api/changes?${q}`);
    const first = feed.seq === undefined;
    feed = { seq: res.seq, epoch: res.epoch };
    if (!first && (res.changes || []).includes('annotations')) refresh();
  } catch { /* try again next time */ }
}
setInterval(pollChanges, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) pollChanges(); });

/** Undo / Redo buttons: the keyboard shortcuts are not there on a tablet. */
function addUndoButtons(): void {
  const tools = document.getElementById('tools-section');
  if (!tools || document.getElementById('anno-undo')) return;
  const mk = (id: string, title: string, d: string, fn: () => void) => {
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
for (const w of document.querySelectorAll<HTMLElement>('.page-wrapper')) addLayer(w);   // pages laid out before this module ran
const ready = load();
ready.then(pollChanges);   // start the change feed from here

// read-only view for the browser checklists
window.zcAnnotations = { ready, list: () => [...byId.values()].map(clone), attachment: () => attId, refresh };

