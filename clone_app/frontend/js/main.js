// Entry point: loads schema + settings, builds the views, wires the toolbar,
// search, status bar and global keyboard shortcuts.
import * as A from './actions.js';
import { api } from './api.js';
import { icon } from './icons.js';
import { emit, on, state } from './store.js';
import { closeMenu, openMenu } from './ui/menu.js';
import { toastError } from './ui/toast.js';
import { debounce, modKey, modLabel, plural, storage, store } from './util.js';
import { openExport } from './views/exportDialog.js';
import { openIdentifierDialog } from './views/identifierDialog.js';
import { openImport } from './views/importDialog.js';
import { focusList, initItemList, openColumnPicker } from './views/itemlist.js';
import { initItemPane, toggleItemPane } from './views/itempane.js';
import { applyAppearance, closeSettings, openSettings, settingsOpen } from './views/settingsView.js';
import { initSidebar } from './views/sidebar.js';

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
  initToolbar();
  initSidebar();
  initItemList();
  initItemPane();
  initResizer();
  initStatusBar();
  initShortcuts();
  if (storage('zc.itemPaneHidden', false)) toggleItemPane(false);

  on('open-import', (file) => openImport(file instanceof File ? file : null));
  on('open-identifier', () => openIdentifierDialog());
  on('set-search-mode', (m) => setMode(m));
  on('clear-search', () => clearSearch());

  await A.loadItems();
  focusList();
  pollStats();
}

/* ---------------------------------------------------------------- toolbar */
let searchInput, modeBtn;

function initToolbar() {
  const tb = document.getElementById('toolbar');
  tb.innerHTML = `
    <div class="brand"><div class="brand-logo">Z</div><span>Library</span></div>
    <button class="icon-btn" data-a="new" title="New Item (${modLabel}N)">${icon('plus')}</button>
    <button class="icon-btn" data-a="identifier" title="Add by DOI, arXiv ID or ISBN (${modLabel}⇧N)">${icon('wand')}</button>
    <span class="toolbar-sep"></span>
    <button class="icon-btn" data-a="import" title="Import BibTeX, RIS, EndNote… (${modLabel}I)">${icon('import')}</button>
    <button class="icon-btn" data-a="export" title="Export (${modLabel}E)">${icon('export')}</button>
    <span class="spacer"></span>
    <div class="search" role="search">
      ${icon('search')}
      <input type="search" id="search-input" placeholder="Search" aria-label="Search library" autocomplete="off" spellcheck="false">
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

  tb.addEventListener('click', (e) => {
    const b = e.target.closest('[data-a]');
    if (!b) return;
    const a = b.dataset.a;
    if (a === 'new') A.newItem();
    if (a === 'identifier') openIdentifier();
    if (a === 'import') openImport();
    if (a === 'export') exportSelectionOrAll();
    if (a === 'settings') openSettings();
    if (a === 'pane') toggleItemPane();
    if (a === 'columns') openColumnPicker(b);
    if (a === 'mode') openModeMenu(b);
    if (a === 'clear-search') clearSearch();
  });

  const run = debounce(() => A.setSearch(searchInput.value.trim()), 220);
  searchInput.addEventListener('input', () => {
    tb.querySelector('[data-a="clear-search"]').hidden = !searchInput.value;
    run();
  });
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); if (searchInput.value) clearSearch(); else focusList(); }
    if (e.key === 'Enter') { run.flush(); focusList(); }
    if (e.key === 'ArrowDown') { e.preventDefault(); focusList(); }
  });
}

function openIdentifier() {
  if (state.view !== 'library') A.setView('library');
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
    { label: 'Tip: "exact phrase", -exclude', disabled: true },
  ], anchor);
}

function clearSearch() {
  searchInput.value = '';
  document.querySelector('[data-a="clear-search"]').hidden = true;
  A.setSearch('');
}

/* --------------------------------------------------------- pane resizer */
function initResizer() {
  const handle = document.getElementById('resizer-right');
  const pane = document.getElementById('item-pane');
  const w = storage('zc.itemPaneWidth', 360);
  pane.style.width = `${w}px`;
  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    const startX = e.clientX, startW = pane.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    const move = (ev) => { pane.style.width = `${Math.min(innerWidth * 0.6, Math.max(260, startW - (ev.clientX - startX)))}px`; };
    const up = () => {
      document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
      handle.classList.remove('dragging'); document.body.style.cursor = '';
      store('zc.itemPaneWidth', pane.offsetWidth);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
  handle.addEventListener('dblclick', () => toggleItemPane());
}

/* ------------------------------------------------------------ status bar */
function initStatusBar() {
  const bar = document.getElementById('statusbar');
  bar.innerHTML = '<span data-s="count"></span><span data-s="sel"></span><span class="spacer"></span><span class="status-index" data-s="index"></span>';
  const paint = () => {
    const total = state.counts[state.view] || 0;
    bar.querySelector('[data-s="count"]').textContent = state.query
      ? `${plural(state.items.length, 'result')} of ${total}` : `${plural(total, 'item')}${state.view === 'trash' ? ' in Trash' : ''}`;
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
