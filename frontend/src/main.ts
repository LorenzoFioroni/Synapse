// Entry point: loads schema + settings, builds the views, wires the toolbar,
// search, status bar and global keyboard shortcuts.
import * as A from './actions.js';
import { api } from './api.js';
import { icon } from './icons.js';
import { currentSource, emit, folderById, on, state } from './store.js';
import { installLongPress } from './ui/longpress.js';
import { closeMenu, openMenu } from './ui/menu.js';
import { initNotifications, startJob, type Job } from './notifications/center.js';
import { toastError } from './ui/toast.js';
import type { Counts, SearchMode } from './types.js';
import { debounce, isMac, modKey, modLabel, plural, storage, store } from './util.js';
import { commands, keyMatches, pages } from './extend.js';
import { initPluginHost } from './plugins/host.js';
import { noteHooks } from './views/notes/hooks.js';
import { type NavEntry, initNav, navPush, navReplace, navSnapshot, navUpdate } from './nav.js';
import { initChanges } from './changes.js';
import { startPresence } from './presence.js';
import {
  closeNoteTabs, homeUnderneath, initTabs, repaintColors, noteEdited, noteModeChanged, notesDeleted as tabsNotesDeleted, notesRenamed as tabsNotesRenamed, openNoteTab, openViewerTab,
  setHomeLabel, showHome, tabShortcut,
} from './tabs/tabs.js';
import { type Mode, folderColor, noteTabs } from './views/notes/workspace.js';
import { openNewWindow, readPlace } from './windows.js';
import { closePage, currentPageId, initPages, openPage, pageOpen } from './views/pages.js';
import { openPalette } from './views/palette.js';
import { openExport } from './views/exportDialog.js';
import { smartFolderDialog } from './views/organizeDialogs.js';
import { openIdentifierDialog } from './views/identifierDialog.js';
import { openImport } from './views/importDialog.js';
import { focusList, initItemList, openColumnPicker } from './views/itemlist.js';
import { initItemPane, toggleItemPane } from './views/itempane.js';
import { applyAppearance, closeSettings, openSettings, settingsOpen } from './views/settingsView.js';
import { initSidebar } from './views/sidebar.js';
import {
  closeNoteTab, closeNotes, flushNote, initNotes, notesOpen, notesPanelButton, notesShortcut, openNotes, showNote,
} from './views/notes/notesView.js';
import { tasksPage } from './tasksPage.js';

function startupStatus(text: string): void {
  const el = document.querySelector<HTMLElement>('#startup-screen .startup-status');
  if (el) el.textContent = text;
}

function finishStartup(): void {
  const el = document.getElementById('startup-screen');
  if (!el) return;
  el.classList.add('ready');
  setTimeout(() => el.remove(), 180);
}

async function boot() {
  try {
    const [schema, settings] = await Promise.all([api.schema(), api.getSettings()]);
    state.schema = schema;
    state.settings = settings.settings;
    state.settingsMeta = settings;
    state.mode = settings.settings.default_search_mode;
  } catch (e) {
    document.getElementById('items-pane')!.innerHTML = `<div class="empty-state">${icon('alert')}<h3>Cannot reach the server</h3><p>${(e as Error).message}</p><p class="muted">Start it with <span class="mono">python server.py</span> and reload.</p></div>`;
    finishStartup();
    return;
  }
  applyAppearance();
  installLongPress();   // touch screens: long press = right-click / hover
  initToolbar();
  initSidebar();
  initDrawer();
  initItemList();
  initItemPane();
  initNotes();
  initPages(async () => {
    showHome();
    if (settingsOpen()) closeSettings();
    if (notesOpen()) await closeNotes({ toLibrary: false });
    tasksPage.close();
  });
  registerCoreCommands();
  initResizer();
  initStatusBar();
  initShortcuts();
  if (storage('zc.itemPaneHidden', false)) toggleItemPane(false);
  // Notes open in tabs: the first tab shows the library or Tasks, a note tab its note.
  initTabs({ home: goHome, note: (path, mode) => showNote(path, mode as Mode | undefined), noteClosed: closeNoteTab, flushNote, noteColor: folderColor });
  on('notes-count', repaintColors);   // the notes listing came again: folder colours may have changed
  Object.assign(noteTabs, { open: openNoteTab, edited: noteEdited, modeChanged: noteModeChanged, renamed: tabsNotesRenamed, deleted: tabsNotesDeleted });
  on('settings', () => { if (state.settings.notes_enabled === false) closeNoteTabs(); });
  startPresence();

  on('open-import', (file) => openImport(file instanceof File ? file : null));
  on('open-identifier', () => openIdentifierDialog());
  on('set-search-mode', (m) => setMode(m));
  on('clear-search', () => clearSearch());
  on('open-notes', () => openNotes());
  on('close-notes', () => switchWorkspace('library'));
  on('open-tasks', (o) => goToTasks(o || {}));
  on('open-note', ({ path, line }) => { showHome(); openNotes(path, line ?? null); });
  on('show-item', (id) => showItem(id));
  on('open-view', (view) => openView(view));
  on('workspace', paintWorkspace);
  on('tasks-page', paintWorkspace);
  // Back/Forward move between notes and the library view you came from.
  initNav({ ws: 'library', view: state.view, sel: [] }, navigate);
  navSnapshot((cur) => (cur.ws === 'library' ? { view: state.view, sel: [...state.selection] } : null));
  const remember = debounce(() => navUpdate('library', { view: state.view, sel: [...state.selection] }), 300);
  on('selection', remember);
  on('view', remember);

  startupStatus('Loading your library…');
  try { await Promise.all([A.loadItems(), new Promise(resolve => setTimeout(resolve, 1000))]); } finally { finishStartup(); }
  initChanges();   // show what other windows and devices change
  pluginsStarted = initPluginHost();   // task deep links wait for the plugin's page
  focusList();
  pollStats();
  openFromHash();
  window.addEventListener('hashchange', openFromHash);
}

let pluginsStarted: Promise<void> = Promise.resolve();

/** The place named after '#' (windows.ts): an item (the browser extension's "Open in Library"),
 *  a folder, a note, a task, and a PDF in a tab — where a new window was asked to open. */
async function openFromHash(): Promise<void> {
  const place = readPlace(location.hash);
  if (!place) return;
  history.replaceState(history.state, '', location.href.split('#')[0]);
  if (place.note && state.settings.notes_enabled !== false) await openNotes(place.note, place.line ?? null, { keep: true });
  else if (place.task) { await pluginsStarted; await goToTasks({ task: place.task }); }
  else {
    if (place.view) { A.setView(place.view); await A.loadItems({ keepSelection: false }); }
    if (place.item) await showItem(place.item);   // in that folder when it is there
  }
  if (place.pdf) openViewerTab(place.pdf);
}

/** The Tasks page (leaving Notes or Settings), optionally with a task selected or a note's tasks. */
async function goToTasks(o: { task?: string | null; note?: string | null } = {}): Promise<void> {
  if (!tasksPage.on) return;
  if (settingsOpen()) closeSettings();
  closePage({ push: false });
  if (notesOpen()) await closeNotes({ toLibrary: false });
  await tasksPage.open(o);
}

/** Save the note before exposing the library; callers record their final view/selection. */
async function leaveForLibrary(): Promise<void> {
  await closeNotes({ toLibrary: false });
  tasksPage.close();
  closePage({ push: false });
  emit('workspace');
}

/** Show one item in the library (leaving Notes, Tasks or Settings), selected and scrolled to. */
async function showItem(id: string): Promise<void> {
  showHome();
  if (settingsOpen()) closeSettings();
  await leaveForLibrary();
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
  navPush({ ws: 'library', view: state.view, sel: [...state.selection] });
}

/** Show a view of the library (a folder, a plugin's list …), leaving Notes, Tasks, Settings or a page. */
async function openView(view: string): Promise<void> {
  showHome();
  if (settingsOpen()) closeSettings();
  await leaveForLibrary();
  if (state.query) clearSearch();
  navPush({ ws: 'library', view, sel: state.view === view ? [...state.selection] : [] });
  A.setView(view);
  focusList();
}

/** Back/Forward: go to a note or to the library view and selection stored in the history entry. */
async function navigate(entry: NavEntry): Promise<void> {
  showHome();
  if (settingsOpen()) closeSettings();
  const unavailable = (entry.ws === 'tasks' && !tasksPage.on)
    || (entry.ws === 'page' && !pages.get(entry.page || ''))
    || (entry.ws === 'notes' && state.settings.notes_enabled === false)
    || (entry.ws === 'library' && entry.view?.startsWith('source:') && !state.sources.some((s) => `source:${s.id}` === entry.view));
  if (unavailable) {
    entry = { ws: 'library', view: 'library', sel: [] };
    navReplace(entry);
  }
  if (entry.ws === 'tasks') { await goToTasks({ task: entry.task || null }); return; }
  if (entry.ws === 'page' && entry.page) {
    if (notesOpen()) await closeNotes({ toLibrary: false });
    tasksPage.close();
    await openPage(entry.page, { push: false });
    return;
  }
  if (entry.ws === 'notes' && entry.graph && !noteHooks.graph) {
    entry = { ...entry, graph: false, focus: null };
    navReplace(entry);
  }
  if (entry.ws === 'notes') { await openNotes(entry.path || null, null, { graph: !!entry.graph, focus: entry.focus || null }); return; }
  await leaveForLibrary();
  if (entry.view && entry.view !== state.view) { A.setView(entry.view); await A.loadItems({ keepSelection: false }); }
  const ids = (entry.sel || []).filter((id) => state.items.some((i) => i.id === id));
  A.select(ids, { anchor: ids[0] || null, cursor: ids[ids.length - 1] || null });
  focusList();
}

/** Library ⇄ Notes (⌘⇧L), or the workspace a toolbar switch button names. */
async function switchWorkspace(to: string = notesOpen() ? 'library' : 'notes'): Promise<void> {
  if (settingsOpen()) closeSettings();
  if (to === 'notes') await openNotes();
  else {
    await leaveForLibrary();
    navPush({ ws: 'library', view: state.view, sel: [...state.selection] });
  }
}

/** What the first tab shows: the library, Tasks or a plugin's page, whichever was shown last. */
let homeWs: 'library' | 'tasks' | 'page' = 'library';

function paintWorkspace() {
  const sw = document.querySelector<HTMLElement>('.ws-switch');
  if (!sw) return;
  sw.hidden = state.settings.notes_enabled === false;
  const ws = notesOpen() ? 'notes' : tasksPage.isOpen() ? 'tasks' : pageOpen() ? 'page' : 'library';
  sw.querySelectorAll<HTMLElement>('[data-ws]').forEach((b) => b.classList.toggle('active', b.dataset.ws === ws));
  if (ws === 'notes') return;
  homeWs = ws;
  const page = ws === 'page' ? pages.get(currentPageId() || '')?.title || 'Library' : '';
  setHomeLabel(ws === 'tasks' ? 'Tasks' : ws === 'page' ? page : 'Library');
  homeUnderneath();
}

/** The first tab was chosen: back to the library or Tasks. */
async function goHome(): Promise<void> {
  if (settingsOpen()) closeSettings();
  if (homeWs === 'tasks') { await goToTasks(); return; }
  const page = homeWs === 'page' ? currentPageId() : null;
  if (page) {
    if (notesOpen()) await closeNotes({ toLibrary: false });
    await openPage(page);
    return;
  }
  await leaveForLibrary();
  navPush({ ws: 'library', view: state.view, sel: [...state.selection] });
  focusList();
}

/* ---------------------------------------------------------------- toolbar */
let searchInput: HTMLInputElement, modeBtn: HTMLElement;

function initToolbar() {
  const tb = document.getElementById('toolbar')!;
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
    <button class="icon-btn notification-bell" data-a="notifications" aria-label="Notifications" aria-expanded="false" aria-controls="notification-center" title="Notifications">${icon('bell')}<span class="notification-badge" hidden></span></button>
    <button class="icon-btn" data-a="settings" title="Settings (${modLabel},)">${icon('gear')}</button>
    <button class="icon-btn mobile-more" data-a="more" title="More actions" aria-label="More actions">${icon('drag')}</button>`;
  initNotifications(tb.querySelector<HTMLButtonElement>('[data-a="notifications"]')!);
  searchInput = tb.querySelector<HTMLInputElement>('#search-input')!;
  modeBtn = tb.querySelector<HTMLElement>('[data-a="mode"]')!;
  paintMode();

  paintWorkspace();
  on('settings', paintWorkspace);
  tb.addEventListener('click', (e) => {
    const target = e.target as Element;
    const ws = target.closest<HTMLElement>('[data-ws]');
    if (ws) { switchWorkspace(ws.dataset.ws); return; }
    const b = target.closest<HTMLElement>('[data-a]');
    if (!b) return;
    const a = b.dataset.a!;
    if (notesOpen() && (a === 'columns' || a === 'pane')) { notesPanelButton(a); return; }   // the notes' own panels
    if (tasksPage.isOpen() && (a === 'columns' || a === 'pane')) { tasksPage.panelButton(a); return; }
    if (pageOpen() && (a === 'columns' || a === 'pane')) return;   // a page has no list or item pane
    if (pageOpen() && ['new', 'identifier', 'import', 'export'].includes(a)) closePage();
    if (notesOpen() && ['new', 'identifier', 'import', 'export'].includes(a)) closeNotes();   // library actions
    if (tasksPage.isOpen() && ['new', 'identifier', 'import', 'export'].includes(a)) tasksPage.close();
    if (a === 'new') A.newItem();
    if (a === 'identifier') openIdentifier();
    if (a === 'import') openImport();
    if (a === 'export') exportSelectionOrAll();
    if (a === 'settings') openSettings();
    if (a === 'more') openMobileMenu(b);
    if (a === 'pane') toggleItemPane();
    if (a === 'sidebar') document.body.classList.toggle('sidebar-open');
    if (a === 'columns') openColumnPicker(b);
    if (a === 'mode') openModeMenu(b);
    if (a === 'clear-search') clearSearch();
    if (a === 'save-search') smartFolderDialog(null, { query: searchInput.value.trim(), mode: state.mode });
  });

  const run = debounce(() => A.setSearch(searchInput.value.trim()), 220);
  searchInput.addEventListener('input', () => {
    tb.querySelector<HTMLElement>('[data-a="clear-search"]')!.hidden = !searchInput.value;
    tb.querySelector<HTMLElement>('[data-a="save-search"]')!.hidden = !searchInput.value.trim();
    run();
  });
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); if (searchInput.value) clearSearch(); else focusList(); }
    if (e.key === 'Enter') { run.flush(); focusList(); }
    if (e.key === 'ArrowDown') { e.preventDefault(); focusList(); }
  });
}

/** Phone toolbars keep frequent actions visible and put the rest in one finger-sized menu. */
function openMobileMenu(anchor: HTMLElement): void {
  openMenu([
    { label: 'New Item', icon: 'plus', run: () => A.newItem() },
    { label: 'Add by Identifier…', icon: 'wand', run: openIdentifier },
    { label: 'Import…', icon: 'import', run: () => openImport() },
    { label: 'Export…', icon: 'export', run: exportSelectionOrAll },
    'sep',
    { label: notesOpen() ? 'Notes List' : tasksPage.isOpen() ? 'Task Views' : 'Choose Columns', icon: 'columns',
      run: () => notesOpen() ? notesPanelButton('columns') : tasksPage.isOpen() ? tasksPage.panelButton('columns') : openColumnPicker(anchor) },
    { label: notesOpen() ? 'Note Details' : tasksPage.isOpen() ? 'Task Details' : 'Item Details', icon: 'sidebar',
      run: () => notesOpen() ? notesPanelButton('pane') : tasksPage.isOpen() ? tasksPage.panelButton('pane') : toggleItemPane() },
    'sep',
    { label: 'Settings', icon: 'gear', run: () => openSettings() },
  ], anchor);
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
  const phone = matchMedia('(max-width: 700px)').matches;
  const short = (phone
    ? { basic: 'Titles', all: 'All fields', fulltext: 'Full text' }
    : { basic: 'Title, Abstract & Authors', all: 'All Fields', fulltext: 'All Fields & Full Text' } as Record<SearchMode, string>)[state.mode];
  modeBtn.innerHTML = `${short} ${icon('chevronDown', 'icon-sm')}`;
  searchInput.placeholder = phone ? 'Search library' : ({ basic: 'Search titles, abstracts and authors', all: 'Search all fields', fulltext: 'Search all fields and PDF text' } as Record<SearchMode, string>)[state.mode];
}

function setMode(m: SearchMode): void {
  state.mode = m;
  paintMode();
  if (state.query) A.setSearch(state.query, m);
}

function openModeMenu(anchor: HTMLElement): void {
  const modes = state.settingsMeta.searchModes || ({} as Record<SearchMode, string>);
  openMenu([
    { section: 'Search in' },
    ...(Object.entries(modes) as [SearchMode, string][]).map(([k, label]) => ({ label, checked: state.mode === k, run: () => { setMode(k); searchInput.focus(); } })),
    'sep',
    { section: 'Filters (combine with text)' },
    ...[['tag:name', 'Tagged with…'], ['folder:name', 'In folder (or subfolder)…'], ['year:2018-2022', 'Year or range (also >2015)'],
      ['type:book', 'Item type…'], ['has:pdf', 'Has a PDF'], ['author:name', 'Author…'],
      ...state.fieldDefs.map((f) => [`${fieldFilterName(f)}:${['int', 'float'].includes(f.type) ? '>10' : 'value'}`, `${f.label} (${f.plugin_name})`]),
    ].map(([f, label]) => ({
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

/** The name a plugin's field is filtered by: its own, unless another plugin or the search itself
    uses it (backend/library/search.py FILTER_KEYS). */
const CORE_FILTERS = new Set(['tag', 'tags', 'folder', 'type', 'year', 'has', 'author', 'title', 'journal', 'doi', 'key']);
function fieldFilterName(f: { id: string; name: string }): string {
  return CORE_FILTERS.has(f.name) || state.fieldDefs.filter((x) => x.name === f.name).length > 1 ? f.id : f.name;
}

function clearSearch() {
  searchInput.value = '';
  document.querySelector<HTMLElement>('[data-a="clear-search"]')!.hidden = true;
  document.querySelector<HTMLElement>('[data-a="save-search"]')!.hidden = true;
  A.setSearch('');
}

/* --------------------------------------------------------- pane resizer */
function initResizer() {
  const handle = document.getElementById('resizer-right')!;
  const pane = document.getElementById('item-pane')!;
  const w = storage('zc.itemPaneWidth', 360);
  pane.style.width = `${w}px`;
  handle.addEventListener('pointerdown', (e) => {   // mouse, finger or Pencil
    if (e.button > 0) return;
    e.preventDefault();
    handle.setPointerCapture?.(e.pointerId);
    const startX = e.clientX, startW = pane.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    const move = (ev: PointerEvent) => { pane.style.width = `${Math.min(innerWidth * 0.6, Math.max(260, startW - (ev.clientX - startX)))}px`; };
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
// from the toolbar button and close again once one is chosen.
const narrow = matchMedia('(max-width: 1100px)');
function initDrawer() {
  const close = () => document.body.classList.remove('sidebar-open');
  document.getElementById('sidebar')!.addEventListener('click', (e) => {
    const t = e.target as Element;
    if (narrow.matches && t.closest('.side-item, [data-view], [data-folder], [data-smart]') && !t.closest('.side-add, .twisty, input')) setTimeout(close, 120);
  });
  document.addEventListener('pointerdown', (e) => {
    if (document.body.classList.contains('sidebar-open') && !(e.target as Element).closest('#sidebar, [data-a="sidebar"], .menu, .dialog-backdrop')) close();
  }, true);
  narrow.addEventListener?.('change', close);
}

/* ------------------------------------------------------------ status bar */
function initStatusBar() {
  const bar = document.getElementById('statusbar')!;
  bar.innerHTML = '<span data-s="count"></span><span data-s="sel"></span><span class="spacer"></span><span class="status-index" data-s="index"></span>';
  const paint = () => {
    const filtered = state.query || state.tagFilter.size;
    let where = ({ trash: ' in Trash', duplicates: ' that look like duplicates' } as Record<string, string>)[state.view] || '';
    if (state.view.startsWith('folder:')) where = ` in ${folderById(state.view.slice(7))?.name || 'folder'}`;
    if (state.view.startsWith('smart:')) where = ` in ${state.sidebar.smart.find((s) => `smart:${s.id}` === state.view)?.name || 'smart folder'}`;
    if (state.view.startsWith('source:')) where = ` in ${currentSource()?.title || 'list'}`;
    const outside = state.view.startsWith('source:') ? state.items.filter((i) => i._outside).length : 0;
    const total = state.counts[state.view as keyof Counts] ?? state.items.length;
    bar.querySelector('[data-s="count"]')!.textContent = filtered
      ? `${plural(state.items.length, 'item')}${where} matching${state.query ? ` “${state.query}”` : ''}${state.tagFilter.size ? ` · tags: ${[...state.tagFilter].join(' + ')}` : ''}`
      : `${plural(state.view in state.counts ? total : state.items.length, 'item')}${where}${outside ? ` · ${outside} not in your library` : ''}`;
    bar.querySelector('[data-s="sel"]')!.textContent = state.selection.size > 1 ? `${state.selection.size} selected` : '';
  };
  on('items', paint);
  on('selection', paint);
  let indexing: Job | null = null;
  on('indexer', () => {
    const ix = state.indexer;
    const el = bar.querySelector('[data-s="index"]')!;
    if (ix?.indexer.running) {
      indexing ||= startJob('Indexing full text…');
      indexing.set(`Indexing full text ${ix.indexer.done}/${ix.indexer.total}`, ix.indexer.done, ix.indexer.total);
      el.textContent = '';
    } else { indexing?.finish('Full-text indexing complete'); indexing = null; el.textContent = ''; }
  });
  on('loading', (l: boolean) => bar.classList.toggle('loading', l));
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
    if (tabShortcut(e)) { e.preventDefault(); return; }
    const target = e.target as HTMLElement;
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable || !!target.closest?.('.cm-editor');
    const k = e.key.toLowerCase();
    if ((modKey(e) && e.shiftKey && k === 'p') || (e.key === 'F1' && !modKey(e))) { e.preventDefault(); if (settingsOpen()) closeSettings(); openPalette(); return; }
    if (pluginKey(e, typing)) { e.preventDefault(); return; }
    if (pageOpen() && !settingsOpen() && ((modKey(e) && ['n', 'i', 'e', 'f'].includes(k)) || (e.key === '/' && !typing))) closePage();   // library actions leave the page
    if (modKey(e) && e.shiftKey && k === 'l' && state.settings.notes_enabled !== false) { e.preventDefault(); switchWorkspace(); return; }
    if (tasksPage.isOpen() && !settingsOpen()) {
      if (modKey(e) && k === ',') { e.preventDefault(); openSettings(); return; }
      if (target.closest?.('.cm-editor')) return;
      if (tasksPage.shortcut(e, k, modKey(e))) return;
      if (modKey(e) && ['n', 'i', 'e'].includes(k)) { e.preventDefault(); return; }
      if (e.key === '/' && !typing) { e.preventDefault(); tasksPage.shortcut(e, 'f', true); return; }
      if (e.key === '/' || e.key === 'Escape') return;
    }
    if (notesOpen() && !settingsOpen()) {
      // The note editor owns its keys (search, undo, …); only Settings stays global.
      if (modKey(e) && k === ',') { e.preventDefault(); openSettings(); return; }
      if (notesShortcut(e, k, modKey(e))) return;
      if (target.closest?.('.cm-editor')) return;
      if (modKey(e) && ['n', 'i', 'e'].includes(k)) { e.preventDefault(); return; }   // library actions: not here
      if (e.key === '/' || e.key === 'Escape') return;
    }
    if (modKey(e) && k === 'f') { e.preventDefault(); if (settingsOpen()) closeSettings(); searchInput.focus(); searchInput.select(); return; }
    if (modKey(e) && k === ',') { e.preventDefault(); if (settingsOpen()) closeSettings(); else openSettings(); return; }
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

/** A plugin's command whose key this is (keys without ⌘/Ctrl wait while typing). */
function pluginKey(e: KeyboardEvent, typing: boolean): boolean {
  if (typing && !modKey(e)) return false;
  for (const c of commands.all()) {
    if (!c.owner || !c.key || !keyMatches(c.key, e, isMac)) continue;
    try { if (c.when && !c.when()) continue; } catch { continue; }
    if (settingsOpen()) closeSettings();
    c.run();
    return true;
  }
  return false;
}

/** Synapse's own commands, for the command palette (their keys are handled above, not from here). */
function registerCoreCommands(): void {
  const notesOn = () => state.settings.notes_enabled !== false;
  const add = (id: string, title: string, run: () => unknown, key?: string, when?: () => boolean) => commands.register({ id: `core.${id}`, title, run, key, when });
  add('new-item', 'New Item', async () => { await switchWorkspace('library'); A.newItem(); }, 'Mod-n');
  add('add-identifier', 'Add by DOI, arXiv ID or ISBN…', async () => { await switchWorkspace('library'); openIdentifier(); }, 'Mod-Shift-n');
  add('import', 'Import…', async () => { await switchWorkspace('library'); openImport(); }, 'Mod-i');
  add('export', 'Export…', () => exportSelectionOrAll(), 'Mod-e');
  add('search', 'Search the Library', async () => { await switchWorkspace('library'); searchInput.focus(); searchInput.select(); }, 'Mod-f');
  add('library', 'Show the Library', () => switchWorkspace('library'));
  add('notes', 'Show Notes', () => switchWorkspace('notes'), undefined, notesOn);
  add('item-pane', 'Show or Hide the Item Pane', () => toggleItemPane(), undefined, () => !notesOpen() && !tasksPage.isOpen() && !pageOpen());
  add('settings', 'Settings', () => openSettings(), 'Mod-,');
  add('settings-plugins', 'Settings: Plugins', () => openSettings('plugins'));
  add('settings-backups', 'Settings: Backups', () => openSettings('backups'));
  add('new-window', 'New Window', () => openNewWindow());
}

boot();
