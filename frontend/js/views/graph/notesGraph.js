// The graph in Notes, Obsidian-style:
//
// - Local graph: at the top of the note's side panel (on by default, can be collapsed). It shows
//   the open note, the notes it links to and from, and the papers it cites; at depth 2 also what
//   those connect to — e.g. the other notes citing the same papers. It follows the open note.
// - Global graph: replaces the editor (button in the notes list header, the local graph's expand
//   button, Cmd/Ctrl+Shift+G, "Show in Graph" in the item and note menus). Notes, and the papers
//   they cite; citations between papers and cited works not in the library can be added.
//
// Clicking a note opens it. Clicking a paper shows a small card (paper note, library, PDF).
// Data: /api/graph (backend/graph.py), filtered with model.js, drawn with forceGraph.js.

import * as A from '../../actions.js';
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { emit } from '../../store.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml as esc, h, shortAuthors, storage, store } from '../../util.js';
import { createGraph } from './forceGraph.js';
import { buildView, matchNodes, relations } from './model.js';

let data = null, dataAt = 0, fetching = null;
let ctx = null;                 // {root, openNote(path), currentPath(), openGraph(focus), onGlobalClosed()}
let local = null, localHost = null, localPath = null;
let global = null, globalEl = null, globalFocus = null;
let card = null;
let localOpts = { depth: 1, papers: true, ...storage('zc.graph.local', {}) };
let globalOpts = { papers: true, citations: false, works: false, orphans: true, ...storage('zc.graph.global', {}) };

/** Graph data, fetched again when it may have changed (after a save) or is older than a minute. */
async function getData(fresh = false) {
  if (!fresh && data && Date.now() - dataAt < 60000) return data;
  fetching ||= api.graph().then((d) => { data = d; dataAt = Date.now(); return d; }).finally(() => { fetching = null; });
  return fetching;
}

/** Call after a note was saved, moved or deleted: the next paint fetches the graph again. */
export function graphChanged() {
  dataAt = 0;
  clearTimeout(graphChanged.t);
  graphChanged.t = setTimeout(() => { if (localPath) paintLocal(); if (global) paintGlobal(); }, 400);
}

export function initNotesGraph(c) {
  ctx = c;
  new MutationObserver(() => { local?.refreshColors(); global?.refreshColors(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && card) closeCard(); });
  document.addEventListener('mousedown', (e) => { if (card && !card.contains(e.target)) closeCard(); });
  window.zcGraph = {   // for tests
    local: () => local, global: () => global, localPath: () => localPath, globalFocus: () => globalFocus,
    get data() { return data; },
  };
}

/* ================================================================ local */
/** The local graph section (rendered once into the side panel; it survives repaints of the rest). */
export function localSection() {
  const open = storage('zc.graph.localOpen', true);
  const el = h(`<div class="ns-section ns-graph${open ? '' : ' collapsed'}">
    <div class="ns-title ns-graph-head">
      <button class="ns-graph-toggle" data-lg="toggle" title="Show or hide the local graph">${icon('chevronDown', 'icon-sm')} Graph</button>
      <span class="spacer"></span>
      <span class="segmented ns-graph-depth" title="How many steps away">
        <button data-lg-depth="1">1</button><button data-lg-depth="2">2</button></span>
      <button class="icon-btn sm" data-lg="papers" title="Show cited papers">${icon('book', 'icon-sm')}</button>
      <button class="icon-btn sm" data-lg="expand" title="Open the full graph (${navigator.platform.includes('Mac') ? '⌘' : 'Ctrl+'}⇧G)">${icon('open', 'icon-sm')}</button>
    </div>
    <div class="ns-graph-canvas"></div>
  </div>`);
  localHost = el.querySelector('.ns-graph-canvas');
  el.addEventListener('click', (e) => {
    const b = e.target.closest('[data-lg],[data-lg-depth]');
    if (!b) return;
    if (b.dataset.lgDepth) { localOpts.depth = Number(b.dataset.lgDepth); store('zc.graph.local', localOpts); paintLocal(); }
    else if (b.dataset.lg === 'papers') { localOpts.papers = !localOpts.papers; store('zc.graph.local', localOpts); paintLocal(); }
    else if (b.dataset.lg === 'expand') ctx.openGraph(localPath ? `note:${localPath}` : null);   // with a history entry
    else if (b.dataset.lg === 'toggle') {
      const nowOpen = el.classList.toggle('collapsed') === false;
      store('zc.graph.localOpen', nowOpen);
      if (nowOpen) paintLocal();
    }
    paintLocalControls(el);
  });
  paintLocalControls(el);
  return el;
}

function paintLocalControls(el) {
  el.querySelectorAll('[data-lg-depth]').forEach((b) => b.classList.toggle('active', Number(b.dataset.lgDepth) === localOpts.depth));
  el.querySelector('[data-lg="papers"]').classList.toggle('on', localOpts.papers);
}

/** Show the neighbourhood of a note (null: nothing open). */
export function showLocal(path) {
  localPath = path;
  paintLocal();
}

async function paintLocal() {
  const section = localHost?.closest('.ns-graph');
  if (!localHost || !localPath || !section || section.classList.contains('collapsed') || !localHost.isConnected) return;
  const path = localPath;
  let d;
  try { d = await getData(); } catch { return; }
  if (path !== localPath || !localHost.isConnected) return;
  const focus = `note:${path}`;
  const view = buildView(d, {
    papers: localOpts.papers, works: false, notes: true, hideIsolated: false, focus, depth: localOpts.depth,
    edges: { cites: false, cite: localOpts.papers, about: localOpts.papers, link: true },
  });
  if (!local || local.host !== localHost) {
    local?.destroy();
    local = await createGraph(localHost, { compact: true, onNodeClick: (n, e) => onNodeClick(n, e, localHost) });
    local.host = localHost;
    new ResizeObserver(() => local?.host === localHost && local.resize()).observe(localHost);
  }
  local.setCurrent(focus);
  local.setData(view);
  local.graph.onEngineStop(() => local.fit(300));
  setTimeout(() => local?.fit(300), 350);
  localHost.dataset.count = String(view.nodes.length);
  localHost.classList.toggle('alone', view.nodes.length <= 1);
}

/* =============================================================== global */
export const globalOpen = () => !!globalEl && !globalEl.hidden;

/** Show the full graph in place of the editor, optionally centred on a node. */
export async function openGlobal(focus = null) {
  const main = ctx.root.querySelector('.notes-main');
  if (!globalEl) {
    globalEl = h(`<div class="notes-graph" hidden>
      <div class="ng-bar">
        <span class="ng-title">${icon('link', 'icon-sm')} Graph</span>
        <div class="ng-search">${icon('search', 'icon-sm')}<input class="input" type="search" placeholder="Find" spellcheck="false" aria-label="Find in graph"></div>
        <span class="spacer"></span>
        <button class="btn btn-sm btn-ghost" data-ng="opts" title="What to show">${icon('gear', 'icon-sm')} Filters</button>
        <button class="icon-btn" data-ng="fit" title="Fit (F)">${icon('columns', 'icon-sm')}</button>
        <button class="btn btn-sm" data-ng="close" title="Back to the note (Esc)">${icon('close', 'icon-sm')} Close</button>
      </div>
      <div class="ng-opts" hidden>
        <label class="ng-opt" title="Papers your notes cite"><input type="checkbox" data-go="papers"> Papers cited in notes</label>
        <label class="ng-opt" title="Citations between papers, from their reference lists (shows every paper)"><input type="checkbox" data-go="citations"> Citations between papers</label>
        <label class="ng-opt sub" title="Works cited by at least two of your papers that are not in your library"><input type="checkbox" data-go="works"> Cited works not in library</label>
        <label class="ng-opt" title="Notes and papers without any links"><input type="checkbox" data-go="orphans"> Orphans</label>
      </div>
      <div class="ng-canvas"></div>
      <div class="ng-empty empty-state" hidden></div>
    </div>`);
    main.appendChild(globalEl);
    globalEl.querySelector('.ng-opts').addEventListener('change', (e) => {
      const k = e.target.dataset.go;
      if (!k) return;
      globalOpts[k] = e.target.checked;
      store('zc.graph.global', globalOpts);
      paintGlobalControls();
      paintGlobal();
    });
    globalEl.querySelector('.ng-bar').addEventListener('click', (e) => {
      const b = e.target.closest('[data-ng]');
      if (b?.dataset.ng === 'close') closeGlobal({ push: true });
      if (b?.dataset.ng === 'fit') global?.fit();
      if (b?.dataset.ng === 'opts') { const o = globalEl.querySelector('.ng-opts'); o.hidden = !o.hidden; b.classList.toggle('active', !o.hidden); }
    });
    const input = globalEl.querySelector('.ng-search input');
    input.addEventListener('input', () => {
      const hits = new Set(matchNodes(global?.nodes() || [], input.value).map((n) => n.id));
      global?.setMatches(input.value.trim() ? hits : null);
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { const first = matchNodes(global?.nodes() || [], input.value)[0]; if (first) global.focus(first.id); }
      if (e.key === 'Escape') { e.stopPropagation(); input.value = ''; global?.setMatches(null); }
    });
    globalEl.addEventListener('keydown', (e) => {
      if (e.target.closest('input')) return;
      if (e.key === 'Escape') closeGlobal({ push: true });
      if (e.key === 'f') global?.fit();
    });
    globalEl.tabIndex = -1;
  }
  globalFocus = focus;
  ctx.root.classList.add('graph-on');
  globalEl.hidden = false;
  paintGlobalControls();
  await paintGlobal({ refit: true });
  globalEl.focus();
}

export function closeGlobal({ push = false } = {}) {
  if (!globalOpen()) return;
  globalEl.hidden = true;
  ctx.root.classList.remove('graph-on');
  closeCard();
  global?.graph.pauseAnimation();
  if (push) ctx.onGlobalClosed?.();
}

function paintGlobalControls() {
  for (const el of globalEl.querySelectorAll('[data-go]')) el.checked = !!globalOpts[el.dataset.go];
  globalEl.querySelector('[data-go="works"]').disabled = !globalOpts.citations;
}

async function paintGlobal({ refit = false } = {}) {
  if (!globalOpen()) return;
  let d;
  try { d = await getData(); } catch (e) { toastError(e); return; }
  const host = globalEl.querySelector('.ng-canvas');
  const view = buildView(d, {
    papers: globalOpts.papers, notes: true, works: globalOpts.papers && globalOpts.citations && globalOpts.works, minCited: 2,
    hideIsolated: !globalOpts.orphans,
    edges: { cites: globalOpts.citations, cite: true, about: true, link: true },
  });
  // Without "Citations between papers", only papers a note cites belong in a notes graph.
  if (!globalOpts.citations) {
    const touched = new Set(view.edges.flatMap((e) => [e.source, e.target]));
    view.nodes = view.nodes.filter((n) => n.kind !== 'paper' || touched.has(n.id) || n.id === globalFocus);
  }
  if (!global) {
    global = await createGraph(host, { onNodeClick: (n, e) => onNodeClick(n, e, host), onBackgroundClick: closeCard });
    new ResizeObserver(() => global?.resize()).observe(host);
  }
  global.graph.resumeAnimation();
  global.setCurrent(ctx.currentPath() ? `note:${ctx.currentPath()}` : null);
  if (refit) global.setMarked(null);
  global.setData(view);
  const empty = globalEl.querySelector('.ng-empty');
  empty.hidden = view.nodes.length > 0;
  empty.innerHTML = `${icon('link')}<h3>No notes yet</h3><p>Link notes with [[Note name]] and cite papers with [@key]; they will show up here.</p>`;
  if (refit) {
    if (globalFocus && view.nodes.some((n) => n.id === globalFocus)) {
      global.setMarked(globalFocus);
      setTimeout(() => global.focus(globalFocus, 500), 700);
    } else setTimeout(() => global.fit(500), 700);
  }
}

/* ================================================================ clicks */
function onNodeClick(n, e, host) {
  closeCard();
  if (n.kind === 'note') { closeGlobal({ push: false }); ctx.openNote(n.path); return; }
  showCard(n, e, host);
}

function showCard(n, e, host) {
  const r = data ? relations(data, n.id) : { notes: [], citedInNotes: [] };
  const paperNote = r.notes[0];
  const ident = n.doi || n.arxiv_id;
  card = h(`<div class="graph-card">
    <div class="gc-kind">${n.kind === 'paper' ? 'In your library' : 'Cited work · not in your library'}</div>
    <div class="gc-title">${esc(n.title || '')}</div>
    <div class="gc-meta">${esc([shortAuthors(n.authors || ''), n.year].filter(Boolean).join(' · '))}${n.citation_key ? ` · <span class="mono">@${esc(n.citation_key)}</span>` : ''}</div>
    ${r.citedInNotes.length ? `<div class="gc-meta">Cited in ${r.citedInNotes.length} note${r.citedInNotes.length > 1 ? 's' : ''}</div>` : ''}
    <div class="gc-actions">
      ${paperNote ? `<button class="btn btn-sm" data-gc="note">${icon('edit', 'icon-sm')} Paper note</button>` : ''}
      ${n.kind === 'paper' ? `<button class="btn btn-sm" data-gc="item">${icon('library', 'icon-sm')} Show in Library</button>` : ''}
      ${n.kind === 'paper' && n.has_pdf ? `<button class="btn btn-sm" data-gc="pdf">${icon('pdf', 'icon-sm')} PDF</button>` : ''}
      ${n.kind === 'work' && ident ? `<button class="btn btn-sm" data-gc="add">${icon('plus', 'icon-sm')} Add to Library</button>` : ''}
    </div></div>`);
  document.body.appendChild(card);
  const hr = host.getBoundingClientRect();
  const x = Math.min((e?.clientX ?? hr.left + hr.width / 2) + 10, window.innerWidth - card.offsetWidth - 8);
  const y = Math.min((e?.clientY ?? hr.top + hr.height / 2) + 10, window.innerHeight - card.offsetHeight - 8);
  card.style.left = `${Math.max(8, x)}px`;
  card.style.top = `${Math.max(8, y)}px`;
  card.addEventListener('click', async (ev) => {
    const b = ev.target.closest('[data-gc]');
    if (!b) return;
    const a = b.dataset.gc;
    if (a === 'note') { closeCard(); closeGlobal({ push: false }); ctx.openNote(paperNote.path); }
    else if (a === 'item') { closeCard(); emit('show-item', n.item_id); }
    else if (a === 'pdf') { closeCard(); A.openPdf(n.item_id); }
    else if (a === 'add') {
      b.disabled = true;
      try {
        await api.fromIdentifier(ident, false, null);
        toast('Added to your library.', { type: 'success' });
        await A.loadItems();
        graphChanged();
        closeCard();
      } catch (err) {
        if (err.status === 409) toast('Already in your library.'); else toastError(err);
        b.disabled = false;
      }
    }
  });
}

function closeCard() { card?.remove(); card = null; }
