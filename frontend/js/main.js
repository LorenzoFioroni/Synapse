// Entry point: loads schema + settings, builds the views, wires the toolbar,
// search, status bar and global keyboard shortcuts.
import * as A from './actions.js';
import { api } from './api.js';
import { icon } from './icons.js';
import { emit, folderById, on, state } from './store.js';
import { installLongPress } from './ui/longpress.js';
import { closeMenu, openMenu } from './ui/menu.js';
import { toastError } from './ui/toast.js';
import { debounce, modKey, modLabel, plural, storage, store } from './util.js';
import { initNav, navSnapshot, navUpdate } from './nav.js';
import { openExport } from './views/exportDialog.js';
import { smartFolderDialog } from './views/organizeDialogs.js';
import { openIdentifierDialog } from './views/identifierDialog.js';
import { openImport } from './views/importDialog.js';
import { focusList, initItemList, openColumnPicker } from './views/itemlist.js';
import { initItemPane, toggleItemPane } from './views/itempane.js';
import { applyAppearance, closeSettings, openSettings, settingsOpen } from './views/settingsView.js';
import { initSidebar } from './views/sidebar.js';
import { closeNotes, initNotes, notesOpen, notesShortcut, openNotes, openNotesGraph, toggleNotesGraph } from './views/notes/notesView.js';

async function boot() {
  try {
    const [schema, settings] = await Promise.all([api.schema(), api.getSettings()]);
    state.schema = schema;
    state.settings = settings.settings;
    state.settingsMeta = settings;
    state.mode = settings.settings.default_search_mode;
  } catch (e) {
    document.getElementById('items-pane').innerHTML = `<div class="empty-state">${icon('alert')}<h3>Cannot reach the server</h3><p>${e.message}</p><p class="muted">Start it with <span class="mono">python server.py</span> and reload.</p></div>`;
    return;
  }
  applyAppearance();
  installLongPress();   // touch screens: long press = right-click / hover (issue #34)
  initToolbar();
  initSidebar();
  initDrawer();
  initItemList();
  initItemPane();
  initNotes();
  initResizer();
  initStatusBar();
  initShortcuts();
  if (storage('zc.itemPaneHidden', false)) toggleItemPane(false);

  on('open-import', (file) => openImport(file instanceof File ? file : null));
  on('open-identifier', () => openIdentifierDialog());
  on('set-search-mode', (m) => setMode(m));
  on('clear-search', () => clearSearch());
  on('open-notes', () => openNotes());
  on('close-notes', () => closeNotes());
  on('open-note', ({ path, line }) => openNotes(path, line));
  on('show-graph', (focus) => { if (settingsOpen()) closeSettings(); openNotesGraph(focus); });
  on('show-item', (id) => showItem(id));
  on('workspace', paintWorkspace);
  // Back/Forward move between notes and the library view you came from.
  initNav({ ws: 'library', view: state.view, sel: [] }, navigate);
  navSnapshot((cur) => (cur.ws === 'library' ? { view: state.view, sel: [...state.selection] } : null));
  const remember = debounce(() => navUpdate('library', { view: state.view, sel: [...state.selection] }), 300);
  on('selection', remember);
  on('view', remember);

  await A.loadItems();
  focusList();
  pollStats();
  openItemFromHash();
  window.addEventListener('hashchange', openItemFromHash);
}

/** #item=<id> (used by the browser extension's "Open in Library") selects that item. */
function openItemFromHash() {
  const id = new URLSearchParams(location.hash.slice(1)).get('item');
  if (!id) return;
  history.replaceState(history.state, '', location.pathname + location.search);
  return showItem(id);
}

/** Show one item in the library (leaving Notes or Settings), selected and scrolled to. */
async function showItem(id) {
  if (settingsOpen()) closeSettings();
  if (notesOpen()) await closeNotes();
  if (!state.items.some((i) => i.id === id)) {
    if (state.query) clearSearch();
    state.tagFilter.clear();
    if (state.view !== 'library') A.setView('library');
    await A.loadItems();
  }
  if (state.items.some((i) => i.id === id)) {
    A.select([id], { anchor: id, cursor: id });
    focusList();
  }
}

/** Back/Forward: go to a note or to the library view and selection stored in the history entry. */
async function navigate(entry) {
  if (settingsOpen()) closeSettings();
  if (entry.ws === 'notes') { await openNotes(entry.path || null, null, { graph: !!entry.graph, focus: entry.focus || null }); return; }
  if (notesOpen()) await closeNotes();
  if (entry.view && entry.view !== state.view) { A.setView(entry.view); await A.loadItems({ keepSelection: false }); }
  const ids = (entry.sel || []).filter((id) => state.items.some((i) => i.id === id));
  if (ids.length) A.select(ids, { anchor: ids[0], cursor: ids[ids.length - 1] });
  focusList();
}

/** Library ⇄ Notes (the toolbar switch and ⌘⇧L). */
function switchWorkspace(to = notesOpen() ? 'library' : 'notes') {
  if (settingsOpen()) closeSettings();
  if (to === 'notes') openNotes(); else closeNotes();
}

function paintWorkspace() {
  const sw = document.querySelector('.ws-switch');
  if (!sw) return;
  sw.hidden = state.settings.notes_enabled === false;
  const ws = notesOpen() ? 'notes' : 'library';
  sw.querySelectorAll('[data-ws]').forEach((b) => b.classList.toggle('active', b.dataset.ws === ws));
}

/* ---------------------------------------------------------------- toolbar */
let searchInput, modeBtn;

function initToolbar() {
  const tb = document.getElementById('toolbar');
  tb.innerHTML = `
    <button class="icon-btn side-toggle" data-a="sidebar" title="Collections" aria-label="Show collections">${icon('menu')}</button>
    <div class="brand"><img class="brand-logo" src="/assets/favicon.svg?v=3" alt="Synapse" title="Synapse" width="24" height="24">
      <div class="segmented ws-switch" role="group" aria-label="Workspace">
        <button data-ws="library" title="Library (${modLabel}⇧L switches)">Library</button>
        <button data-ws="notes" title="Notes (${modLabel}⇧L switches)">Notes</button>
      </div></div>
    <button class="icon-btn" data-a="new" title="New Item (${modLabel}N)">${icon('plus')}</button>
    <button class="icon-btn" data-a="identifier" title="Add by DOI, arXiv ID or ISBN (${modLabel}⇧N)">${icon('wand')}</button>
    <span class="toolbar-sep"></span>
    <button class="icon-btn" data-a="import" title="Import BibTeX, RIS, EndNote… (${modLabel}I)">${icon('import')}</button>
    <button class="icon-btn" data-a="export" title="Export (${modLabel}E)">${icon('export')}</button>
    <span class="spacer"></span>
    <div class="search" role="search">
      ${icon('search')}
      <input type="search" id="search-input" placeholder="Search" aria-label="Search library" autocomplete="off" spellcheck="false">
      <button class="search-clear" data-a="save-search" title="Save this search as a smart folder" hidden>${icon('smartFolder', 'icon-sm')}</button>
      <button class="search-clear" data-a="clear-search" title="Clear (Esc)" hidden>${icon('close', 'icon-sm')}</button>
      <button class="search-mode" data-a="mode" title="Choose what to search"></button>
    </div>
    <span class="spacer"></span>
    <button class="icon-btn" data-a="columns" title="Choose columns">${icon('columns')}</button>
    <button class="icon-btn" data-a="pane" title="Toggle item pane">${icon('sidebar')}</button>
    <button class="icon-btn" data-a="settings" title="Settings (${modLabel},)">${icon('gear')}</button>`;
  searchInput = tb.querySelector('#search-input');
  modeBtn = tb.querySelector('[data-a="mode"]');
  paintMode();

  paintWorkspace();
  on('settings', paintWorkspace);
  tb.addEventListener('click', (e) => {
    const ws = e.target.closest('[data-ws]');
    if (ws) { switchWorkspace(ws.dataset.ws); return; }
    const b = e.target.closest('[data-a]');
    if (!b) return;
    const a = b.dataset.a;
    if (notesOpen() && ['new', 'identifier', 'import', 'export', 'columns', 'pane'].includes(a)) closeNotes();   // library actions
    if (a === 'new') A.newItem();
    if (a === 'identifier') openIdentifier();
    if (a === 'import') openImport();
    if (a === 'export') exportSelectionOrAll();
    if (a === 'settings') openSettings();
    if (a === 'pane') toggleItemPane();
    if (a === 'sidebar') document.body.classList.toggle('sidebar-open');
    if (a === 'columns') openColumnPicker(b);
    if (a === 'mode') openModeMenu(b);
    if (a === 'clear-search') clearSearch();
    if (a === 'save-search') smartFolderDialog(null, { query: searchInput.value.trim(), mode: state.mode });
  });

  const run = debounce(() => A.setSearch(searchInput.value.trim()), 220);
  searchInput.addEventListener('input', () => {
    tb.querySelector('[data-a="clear-search"]').hidden = !searchInput.value;
    tb.querySelector('[data-a="save-search"]').hidden = !searchInput.value.trim();
    run();
  });
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); if (searchInput.value) clearSearch(); else focusList(); }
    if (e.key === 'Enter') { run.flush(); focusList(); }
    if (e.key === 'ArrowDown') { e.preventDefault(); focusList(); }
  });
}

function openIdentifier() {
  if (state.view !== 'library' && !state.view.startsWith('folder:')) A.setView('library');
  openIdentifierDialog();
}

function exportSelectionOrAll() {
  const ids = [...state.selection];
  openExport(ids.length > 1 ? ids : null);
}

function paintMode() {
  const short = { basic: 'Title, Abstract & Authors', all: 'All Fields', fulltext: 'All Fields & Full Text' }[state.mode];
  modeBtn.innerHTML = `${short} ${icon('chevronDown', 'icon-sm')}`;
  searchInput.placeholder = { basic: 'Search titles, abstracts and authors', all: 'Search all fields', fulltext: 'Search all fields and PDF text' }[state.mode];
}

function setMode(m) {
  state.mode = m;
  paintMode();
  if (state.query) A.setSearch(state.query, m);
}

function openModeMenu(anchor) {
  const modes = state.settingsMeta.searchModes;
  openMenu([
    { section: 'Search in' },
    ...Object.entries(modes).map(([k, label]) => ({ label, checked: state.mode === k, run: () => { setMode(k); searchInput.focus(); } })),
    'sep',
    { section: 'Filters (combine with text)' },
    ...[['tag:name', 'Tagged with…'], ['folder:name', 'In folder (or subfolder)…'], ['year:2018-2022', 'Year or range (also >2015)'],
      ['type:book', 'Item type…'], ['has:pdf', 'Has a PDF'], ['author:name', 'Author…']].map(([f, label]) => ({
      label: `${f}  —  ${label}`, run: () => {
        const v = searchInput.value.trim();
        searchInput.value = `${v ? `${v} ` : ''}${f.split(':')[0]}:`;
        searchInput.focus();
        searchInput.dispatchEvent(new Event('input'));
      },
    })),
    { label: 'Tip: "exact phrase", -exclude, -tag:read', disabled: true },
  ], anchor);
}

function clearSearch() {
  searchInput.value = '';
  document.querySelector('[data-a="clear-search"]').hidden = true;
  document.querySelector('[data-a="save-search"]').hidden = true;
  A.setSearch('');
}

/* --------------------------------------------------------- pane resizer */
function initResizer() {
  const handle = document.getElementById('resizer-right');
  const pane = document.getElementById('item-pane');
  const w = storage('zc.itemPaneWidth', 360);
  pane.style.width = `${w}px`;
  handle.addEventListener('pointerdown', (e) => {   // mouse, finger or Pencil
    if (e.button > 0) return;
    e.preventDefault();
    handle.setPointerCapture?.(e.pointerId);
    const startX = e.clientX, startW = pane.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    const move = (ev) => { pane.style.width = `${Math.min(innerWidth * 0.6, Math.max(260, startW - (ev.clientX - startX)))}px`; };
    const up = () => {
      handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', up); handle.removeEventListener('pointercancel', up);
      handle.classList.remove('dragging'); document.body.style.cursor = '';
      store('zc.itemPaneWidth', pane.offsetWidth);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
  });
  handle.addEventListener('dblclick', () => toggleItemPane());
}

/* ------------------------------------------------- sidebar as a drawer */
// On narrow screens (an iPad in portrait, a small window) the collections slide in over the list
// from the toolbar button and close again once one is chosen (issue #34).
const narrow = matchMedia('(max-width: 1100px)');
function initDrawer() {
  const close = () => document.body.classList.remove('sidebar-open');
  document.getElementById('sidebar').addEventListener('click', (e) => {
    if (narrow.matches && e.target.closest('.side-item, [data-view], [data-folder], [data-smart]') && !e.target.closest('.side-add, .twisty, input')) setTimeout(close, 120);
  });
  document.addEventListener('pointerdown', (e) => {
    if (document.body.classList.contains('sidebar-open') && !e.target.closest('#sidebar, [data-a="sidebar"], .menu, .dialog-backdrop')) close();
  }, true);
  narrow.addEventListener?.('change', close);
}

/* ------------------------------------------------------------ status bar */
function initStatusBar() {
  const bar = document.getElementById('statusbar');
  bar.innerHTML = '<span data-s="count"></span><span data-s="sel"></span><span class="spacer"></span><span class="status-index" data-s="index"></span>';
  const paint = () => {
    const filtered = state.query || state.tagFilter.size;
    let where = { trash: ' in Trash', duplicates: ' that look like duplicates'}[state.view] || '';
    if (state.view.startsWith('folder:')) where = ` in ${folderById(state.view.slice(7))?.name || 'folder'}`;
    if (state.view.startsWith('smart:')) where = ` in ${state.sidebar.smart.find((s) => `smart:${s.id}` === state.view)?.name || 'smart folder'}`;
    const total = state.counts[state.view] ?? state.items.length;
    bar.querySelector('[data-s="count"]').textContent = filtered
      ? `${plural(state.items.length, 'item')}${where} matching${state.query ? ` “${state.query}”` : ''}${state.tagFilter.size ? ` · tags: ${[...state.tagFilter].join(' + ')}` : ''}`
      : `${plural(state.view in state.counts ? total : state.items.length, 'item')}${where}`;
    bar.querySelector('[data-s="sel"]').textContent = state.selection.size > 1 ? `${state.selection.size} selected` : '';
  };
  on('items', paint);
  on('selection', paint);
  on('indexer', () => {
    const ix = state.indexer;
    const el = bar.querySelector('[data-s="index"]');
    if (ix?.indexer.running) {
      el.innerHTML = `<span class="spinner"></span> Indexing full text ${ix.indexer.done}/${ix.indexer.total} <span class="progress"><div style="width:${(ix.indexer.done / Math.max(1, ix.indexer.total)) * 100}%"></div></span>`;
    } else el.textContent = '';
  });
  on('loading', (l) => bar.classList.toggle('loading', l));
}

async function pollStats() {
  let delay = 15000;
  try {
    const st = await api.stats();
    const wasRunning = state.indexer?.indexer.running;
    state.indexer = st;
    emit('indexer');
    if (st.indexer.running) delay = 1500;
    // when indexing finishes during a full-text search, refresh results
    if (wasRunning && !st.indexer.running && state.query && state.mode === 'fulltext') A.loadItems();
  } catch { delay = 30000; }
  setTimeout(pollStats, delay);
}
on('items', () => { if (state.indexer && !state.indexer.indexer.running) setTimeout(() => api.stats().then((st) => { state.indexer = st; emit('indexer'); }).catch(() => {}), 1200); });

/* ------------------------------------------------------------- shortcuts */
function initShortcuts() {
  document.addEventListener('keydown', (e) => {
    if (document.querySelector('.dialog-backdrop')) return;
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.target.isContentEditable;
    const k = e.key.toLowerCase();
    if (modKey(e) && e.shiftKey && k === 'l' && state.settings.notes_enabled !== false) { e.preventDefault(); switchWorkspace(); return; }
    if (modKey(e) && e.shiftKey && k === 'g' && state.settings.notes_enabled !== false) { e.preventDefault(); if (settingsOpen()) closeSettings(); toggleNotesGraph(); return; }
    if (notesOpen() && !settingsOpen()) {
      // The note editor owns its keys (search, undo, …); only Settings stays global.
      if (modKey(e) && k === ',') { e.preventDefault(); openSettings(); return; }
      if (notesShortcut(e, k, modKey(e))) return;
      if (e.target.closest?.('.cm-editor')) return;
      if (modKey(e) && ['n', 'i', 'e'].includes(k)) { e.preventDefault(); return; }   // library actions: not here
      if (e.key === '/' || e.key === 'Escape') return;
    }
    if (modKey(e) && k === 'f') { e.preventDefault(); if (settingsOpen()) closeSettings(); searchInput.focus(); searchInput.select(); return; }
    if (modKey(e) && k === ',') { e.preventDefault(); settingsOpen() ? closeSettings() : openSettings(); return; }
    if (settingsOpen()) { if (e.key === 'Escape' && !typing) closeSettings(); return; }
    if (modKey(e) && k === 'n' && e.shiftKey) { e.preventDefault(); openIdentifier(); return; }
    if (modKey(e) && k === 'n') { e.preventDefault(); A.newItem(); return; }
    if (modKey(e) && k === 'i' && !typing) { e.preventDefault(); openImport(); return; }
    if (modKey(e) && k === 'e' && !typing) { e.preventDefault(); exportSelectionOrAll(); return; }
    if (e.key === 'Escape' && !typing) { closeMenu(); if (state.query) clearSearch(); }
    if (e.key === '/' && !typing) { e.preventDefault(); searchInput.focus(); }
  });
  // never let the browser navigate away when a file is dropped outside a drop zone
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => e.preventDefault());
  window.addEventListener('unhandledrejection', (e) => { if (e.reason?.message) toastError(e.reason); });
}

boot();
