// The graph in Notes, Obsidian-style:
//
// - Local graph: at the top of the note's side panel (on by default, can be collapsed). It shows
//   the open note, the notes it links to and from, and the papers it cites; at depth 2 also what
//   those connect to — e.g. the other notes citing the same papers. It follows the open note.
// - Global graph: replaces the editor (button in the notes list header, the local graph's expand
//   button, Cmd/Ctrl+Shift+G, "Show in Graph" in the item and note menus). Notes, and the papers
//   they cite; citations between papers and cited works not in the library can be added. It is
//   centred on the node it was opened for and shows only what is up to 1, 2 or 3 links away from
//   it (or All): the whole library at once is a tangle nobody can read.
//
// Clicking a note opens it. Clicking a paper shows a small card (paper note, library, PDF).
// Data: the plugin's /data route (plugins/graph/server/build.py), filtered with model.ts, drawn
// with forceGraph.ts. The public Notes visualization API owns layout and navigation.

import type { PluginContext, NotesVisualizationContext } from '../../../frontend/src/plugins/api.js';
import { icon, toast, toastError, escapeHtml as esc, h, shortAuthors, storage, store } from './ui.js';
import type { GraphData } from './types.js';
import { type GNode, type GraphCanvas, createGraph } from './forceGraph.js';
import { buildView, label, matchNodes, relations } from './model.js';


type LocalOptions = { depth: number; papers: boolean };
type GlobalOptions = { papers: boolean; citations: boolean; works: boolean; orphans: boolean; depth: number };   // depth 0: all

let data: GraphData | null = null, dataAt = 0, fetching: Promise<GraphData> | null = null;
let ctx: NotesVisualizationContext;
let plugin: PluginContext;
let disposed = false, localPaint = 0, globalPaint = 0;
const observers = new Map<HTMLElement, ResizeObserver>();
const timers = new Set<ReturnType<typeof setTimeout>>();
const later = (fn: () => void, ms: number) => {
  const timer = setTimeout(() => { timers.delete(timer); if (!disposed) fn(); }, ms);
  timers.add(timer); return timer;
};
export function configure(p: PluginContext): void { plugin = p; }
function observe(host: HTMLElement, resize: () => void): void {
  observers.get(host)?.disconnect();
  const observer = new ResizeObserver(resize); observers.set(host, observer); observer.observe(host);
}
let local: (GraphCanvas & { host?: HTMLElement }) | null = null, localHost: HTMLElement | null = null, localPath: string | null = null;
let global: GraphCanvas | null = null, globalEl: HTMLElement | null = null, globalFocus: string | null = null;
let card: HTMLElement | null = null;
let changedTimer: ReturnType<typeof setTimeout> | undefined;
const localOpts: LocalOptions = { depth: 1, papers: true, ...storage<Partial<LocalOptions>>('zc.graph.local', {}) };
const globalOpts: GlobalOptions = { papers: true, citations: false, works: false, orphans: true, depth: 2, ...storage<Partial<GlobalOptions>>('zc.graph.global', {}) };

/** Graph data, fetched again when it may have changed (after a save) or is older than a minute. */
async function getData(fresh = false): Promise<GraphData> {
  if (!fresh && data && Date.now() - dataAt < 60000) return data;
  fetching ||= plugin.backend.get<GraphData>('/data').then((d) => { if (!disposed) { data = d; dataAt = Date.now(); } return d; }).finally(() => { fetching = null; });
  return fetching;
}

/** Call after a note was saved, moved or deleted: the next paint fetches the graph again. */
export function graphChanged(): void {
  if (disposed) return;
  dataAt = 0;
  clearTimeout(changedTimer);
  changedTimer = later(() => { if (localPath) paintLocal(); if (global) paintGlobal(); }, 400);
}

let themeObserver: MutationObserver | null = null;
const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && card) closeCard(); };
const onDown = (e: MouseEvent) => { if (card && !card.contains(e.target as Node)) closeCard(); };

export function initNotesGraph(c: NotesVisualizationContext): void {
  ctx = c;
  themeObserver?.disconnect();
  themeObserver = new MutationObserver(() => { local?.refreshColors(); global?.refreshColors(); });
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  window.addEventListener('keydown', onKey);
  document.addEventListener('mousedown', onDown);
  (window as Window & { zcGraph?: unknown }).zcGraph = {   // for tests
    local: () => local, global: () => global, localPath: () => localPath, globalFocus: () => globalFocus,
    get data() { return data; },
  };
}

/** The plugin stops: take the graphs away. */
export function teardownNotesGraph(): void {
  closeGlobal();
  disposed = true; localPaint++; globalPaint++;
  for (const observer of observers.values()) observer.disconnect();
  observers.clear();
  for (const timer of timers) clearTimeout(timer);
  timers.clear();
  clearTimeout(changedTimer);
  themeObserver?.disconnect();
  themeObserver = null;
  window.removeEventListener('keydown', onKey);
  document.removeEventListener('mousedown', onDown);
  local?.destroy();
  global?.destroy();
  localHost?.closest('.ns-graph')?.remove();
  globalEl?.remove();
  local = global = null;
  localHost = globalEl = null;
  localPath = null;
  data = null;
}

/* ================================================================ local */
/** The local graph section (rendered once into the side panel; it survives repaints of the rest). */
export function localSection(): HTMLElement {
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
  localHost = el.querySelector<HTMLElement>('.ns-graph-canvas');
  el.addEventListener('click', (e) => {
    const b = (e.target as Element).closest<HTMLElement>('[data-lg],[data-lg-depth]');
    if (!b) return;
    if (b.dataset.lgDepth) { localOpts.depth = Number(b.dataset.lgDepth); store('zc.graph.local', localOpts); paintLocal(); }
    else if (b.dataset.lg === 'papers') { localOpts.papers = !localOpts.papers; store('zc.graph.local', localOpts); paintLocal(); }
    else if (b.dataset.lg === 'expand') ctx.open(localPath ? `note:${localPath}` : null);   // with a history entry
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

function paintLocalControls(el: HTMLElement): void {
  el.querySelectorAll<HTMLElement>('[data-lg-depth]').forEach((b) => b.classList.toggle('active', Number(b.dataset.lgDepth) === localOpts.depth));
  el.querySelector('[data-lg="papers"]')!.classList.toggle('on', localOpts.papers);
}

/** Show the neighbourhood of a note (null: nothing open). */
export function showLocal(path: string | null): void {
  localPath = path;
  paintLocal();
}

async function paintLocal() {
  const paint = ++localPaint;
  if (disposed) return;
  const section = localHost?.closest('.ns-graph');
  if (!localHost || !localPath || !section || section.classList.contains('collapsed') || !localHost.isConnected) return;
  const host = localHost;
  const path = localPath;
  let d: GraphData;
  try { d = await getData(); } catch { return; }
  if (disposed || paint !== localPaint || path !== localPath || !host.isConnected) return;
  const focus = `note:${path}`;
  const view = buildView(d, {
    papers: localOpts.papers, works: false, notes: true, hideIsolated: false, focus, depth: localOpts.depth,
    edges: { cites: false, cite: localOpts.papers, about: localOpts.papers, link: true },
  });
  if (!local || local.host !== localHost) {
    local?.destroy();
    if (local?.host) { observers.get(local.host)?.disconnect(); observers.delete(local.host); }
    const g: GraphCanvas & { host?: HTMLElement } = await createGraph(host, { compact: true, onNodeClick: (n, e) => onNodeClick(n, e, host) });
    if (disposed || paint !== localPaint || !host.isConnected) { g.destroy(); return; }
    g.host = host;
    local = g;
    observe(host, () => { if (local?.host === host) local.resize(); });
  }
  const g = local;
  g.setCurrent(focus);
  g.setData(view);
  g.graph.onEngineStop(() => g.fit(300));
  later(() => local?.fit(300), 350);
  host.dataset.count = String(view.nodes.length);
  host.classList.toggle('alone', view.nodes.length <= 1);
}

/* =============================================================== global */
export const globalOpen = () => !!globalEl && !globalEl.hidden;

/** Show the full graph in place of the editor, optionally centred on a node. */
export async function openGlobal(focus: string | null = null): Promise<void> {
  if (disposed) return;
  const main = ctx.main;
  if (!globalEl) {
    const el = h(`<div class="notes-graph" hidden>
      <div class="ng-bar">
        <span class="ng-title">${icon('link', 'icon-sm')} Graph</span>
        <div class="ng-search">${icon('search', 'icon-sm')}<input class="input" type="search" placeholder="Find" spellcheck="false" aria-label="Find in graph"></div>
        <span class="segmented ng-depth" title="Show only what is this many links away from the centre">
          <button data-ng-depth="1">1</button><button data-ng-depth="2">2</button><button data-ng-depth="3">3</button><button data-ng-depth="0">All</button></span>
        <span class="ng-centre" hidden></span>
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
    globalEl = el;
    main.appendChild(el);
    el.querySelector('.ng-opts')!.addEventListener('change', (e) => {
      const box = e.target as HTMLInputElement;
      const k = box.dataset.go as Exclude<keyof GlobalOptions, 'depth'> | undefined;
      if (!k) return;
      globalOpts[k] = box.checked;
      store('zc.graph.global', globalOpts);
      paintGlobalControls();
      paintGlobal();
    });
    el.querySelector('.ng-bar')!.addEventListener('click', (e) => {
      const depth = (e.target as Element).closest<HTMLElement>('[data-ng-depth]');
      if (depth) { setDepth(Number(depth.dataset.ngDepth)); return; }
      const b = (e.target as Element).closest<HTMLElement>('[data-ng]');
      if (b?.dataset.ng === 'close') closeGlobal({ push: true });
      if (b?.dataset.ng === 'fit') global?.fit();
      if (b?.dataset.ng === 'opts') { const o = el.querySelector<HTMLElement>('.ng-opts')!; o.hidden = !o.hidden; b.classList.toggle('active', !o.hidden); }
    });
    const input = el.querySelector<HTMLInputElement>('.ng-search input')!;
    input.addEventListener('input', () => {
      const hits = new Set(matchNodes(global?.nodes() || [], input.value).map((n) => n.id));
      global?.setMatches(input.value.trim() ? hits : null);
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { const first = matchNodes(global?.nodes() || [], input.value)[0]; if (first) global!.focus(first.id); }
      if (e.key === 'Escape') { e.stopPropagation(); input.value = ''; global?.setMatches(null); }
    });
    el.addEventListener('keydown', (e) => {
      if ((e.target as Element).closest('input')) return;
      if (e.key === 'Escape') closeGlobal({ push: true });
      if (e.key === 'f') global?.fit();
    });
    el.tabIndex = -1;
  }
  globalFocus = focus;
  globalEl.hidden = false;
  ctx.setOpen(true);
  paintGlobalControls();
  await paintGlobal({ refit: true });
  globalEl?.focus();
}

export function closeGlobal({ push = false } = {}): void {
  if (!globalOpen()) return;
  globalEl!.hidden = true;
  ctx.setOpen(false);
  closeCard();
  global?.graph.pauseAnimation();
  if (push) ctx.closed();
}

function setDepth(depth: number): void {
  globalOpts.depth = depth;
  store('zc.graph.global', globalOpts);
  paintGlobalControls();
  paintGlobal({ refit: true });
}

/** Centre the full graph on a node, keeping the depth (or the default one when showing all). */
function centreOn(id: string): void {
  globalFocus = id;
  if (!globalOpts.depth) globalOpts.depth = 2;
  store('zc.graph.global', globalOpts);
  paintGlobalControls();
  paintGlobal({ refit: true });
}

function paintGlobalControls(): void {
  for (const el of globalEl!.querySelectorAll<HTMLInputElement>('[data-go]')) el.checked = !!globalOpts[el.dataset.go as keyof GlobalOptions];
  globalEl!.querySelector<HTMLInputElement>('[data-go="works"]')!.disabled = !globalOpts.citations;
  globalEl!.querySelectorAll<HTMLElement>('[data-ng-depth]').forEach((b) => b.classList.toggle('active', Number(b.dataset.ngDepth) === globalOpts.depth));
}

async function paintGlobal({ refit = false } = {}): Promise<void> {
  const paint = ++globalPaint;
  if (disposed || !globalOpen()) return;
  const root = globalEl!;
  let d: GraphData;
  try { d = await getData(); } catch (e) { if (!disposed) toastError(e); return; }
  if (disposed || paint !== globalPaint || root !== globalEl || !globalOpen()) return;
  const host = root.querySelector<HTMLElement>('.ng-canvas')!;
  const centreNode = globalFocus ? d.nodes.find((n) => n.id === globalFocus) : undefined;
  const levels = !!centreNode && globalOpts.depth > 0;
  const view = buildView(d, {
    papers: globalOpts.papers, notes: true, works: globalOpts.papers && globalOpts.citations && globalOpts.works, minCited: 2,
    hideIsolated: !globalOpts.orphans,
    edges: { cites: globalOpts.citations, cite: true, about: true, link: true },
    focus: levels ? globalFocus : null, depth: globalOpts.depth,
  });
  // Without "Citations between papers", only papers a note cites belong in a notes graph.
  if (!globalOpts.citations) {
    const touched = new Set(view.edges.flatMap((e) => [e.source, e.target]));
    view.nodes = view.nodes.filter((n) => n.kind !== 'paper' || touched.has(n.id) || n.id === globalFocus);
  }
  const centreEl = root.querySelector<HTMLElement>('.ng-centre')!;
  centreEl.hidden = !levels;
  if (centreNode) {
    centreEl.innerHTML = `Around <b>${esc(label(centreNode))}</b>`;
    centreEl.title = `${centreNode.title || label(centreNode)} — Shift-click a node to centre on it`;
  }
  if (!global) {
    const created = await createGraph(host, { onNodeClick: (n, e) => onNodeClick(n, e, host), onBackgroundClick: closeCard });
    if (disposed || paint !== globalPaint || root !== globalEl || !globalOpen()) { created.destroy(); return; }
    global = created;
    observe(host, () => global?.resize());
  }
  const g = global;
  g.graph.resumeAnimation();
  g.setCurrent(ctx.currentPath() ? `note:${ctx.currentPath()}` : null);
  if (refit) g.setMarked(null);
  g.setData(view);
  const empty = root.querySelector<HTMLElement>('.ng-empty')!;
  empty.hidden = view.nodes.length > 0;
  empty.innerHTML = `${icon('link')}<h3>No notes yet</h3><p>Link notes with [[Note name]] and cite papers with [@key]; they will show up here.</p>`;
  if (refit) {
    const focus = globalFocus;
    if (levels) {   // a neighbourhood: show all of it, the centre ringed
      g.setMarked(focus);
      later(() => g.fit(500), 700);
    } else if (focus && view.nodes.some((n) => n.id === focus)) {
      g.setMarked(focus);
      later(() => g.focus(focus, 500), 700);
    } else later(() => g.fit(500), 700);
  }
}

/* ================================================================ clicks */
function onNodeClick(n: GNode, e: MouseEvent | undefined, host: HTMLElement): void {
  closeCard();
  if (e?.shiftKey && host === globalEl?.querySelector('.ng-canvas')) { centreOn(n.id); return; }
  if (n.kind === 'note') { closeGlobal({ push: false }); ctx.openNote(n.path!); return; }
  showCard(n, e, host);
}

function showCard(n: GNode, e: MouseEvent | undefined, host: HTMLElement): void {
  const r = data ? relations(data, n.id) : { notes: [], citedInNotes: [] };
  const paperNote = r.notes[0];
  const ident = n.doi || n.arxiv_id;
  const inGlobal = host === globalEl?.querySelector('.ng-canvas');
  const card_ = h(`<div class="graph-card">
    <div class="gc-kind">${n.kind === 'paper' ? 'In your library' : 'Cited work · not in your library'}</div>
    <div class="gc-title">${esc(n.title || '')}</div>
    <div class="gc-meta">${esc([shortAuthors(n.authors || ''), n.year].filter(Boolean).join(' · '))}${n.citation_key ? ` · <span class="mono">@${esc(n.citation_key)}</span>` : ''}</div>
    ${r.citedInNotes.length ? `<div class="gc-meta">Cited in ${r.citedInNotes.length} note${r.citedInNotes.length > 1 ? 's' : ''}</div>` : ''}
    <div class="gc-actions">
      ${paperNote ? `<button class="btn btn-sm" data-gc="note">${icon('edit', 'icon-sm')} Paper note</button>` : ''}
      ${n.kind === 'paper' ? `<button class="btn btn-sm" data-gc="item">${icon('library', 'icon-sm')} Show in Library</button>` : ''}
      ${n.kind === 'paper' && n.has_pdf ? `<button class="btn btn-sm" data-gc="pdf">${icon('pdf', 'icon-sm')} PDF</button>` : ''}
      ${n.kind === 'work' && ident ? `<button class="btn btn-sm" data-gc="add">${icon('plus', 'icon-sm')} Add to Library</button>` : ''}
      ${inGlobal && n.id !== globalFocus ? `<button class="btn btn-sm" data-gc="centre">${icon('link', 'icon-sm')} Centre graph here</button>` : ''}
    </div></div>`);
  card = card_;
  document.body.appendChild(card_);
  const hr = host.getBoundingClientRect();
  const x = Math.min((e?.clientX ?? hr.left + hr.width / 2) + 10, window.innerWidth - card_.offsetWidth - 8);
  const y = Math.min((e?.clientY ?? hr.top + hr.height / 2) + 10, window.innerHeight - card_.offsetHeight - 8);
  card_.style.left = `${Math.max(8, x)}px`;
  card_.style.top = `${Math.max(8, y)}px`;
  card_.addEventListener('click', async (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>('[data-gc]');
    if (!b) return;
    const a = b.dataset.gc;
    if (a === 'note') { closeCard(); closeGlobal({ push: false }); ctx.openNote(paperNote.path!); }
    else if (a === 'item' && n.item_id) { closeCard(); plugin.library.show(n.item_id); }
    else if (a === 'pdf') { closeCard(); plugin.library.openPdf(n.item_id!); }
    else if (a === 'centre') { closeCard(); centreOn(n.id); }
    else if (a === 'add') {
      b.disabled = true;
      try {
        const result = await plugin.backend.post<{ duplicate?: boolean }>('/add', { doi: n.doi, arxiv: n.arxiv_id, title: n.title, authors: n.authors, year: n.year });
        if (disposed) return;
        toast(result.duplicate ? 'Already in your library.' : 'Added to your library.', { type: 'success' });
        await plugin.library.refresh();
        graphChanged();
        closeCard();
      } catch (err) {
        if (!disposed) toastError(err);
        b.disabled = false;
      }
    }
  });
}

function closeCard() { card?.remove(); card = null; }
