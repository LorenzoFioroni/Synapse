// Workspace-specific actions and search, with library queries retained while away.
import * as A from '../actions.js';
import { icon } from '../icons.js';
import { on, state } from '../store.js';
import { initNotifications } from '../notifications/center.js';
import { phoneScreen, switchLayout } from '../phone/layout.js';
import { openMenu, closeMenu } from '../ui/menu.js';
import type { SearchMode } from '../types.js';
import { debounce, modLabel } from '../util.js';
import { tasksPage } from '../tasksPage.js';
import { openExport } from './exportDialog.js';
import { smartFolderDialog } from './organizeDialogs.js';
import { openIdentifierDialog } from './identifierDialog.js';
import { openImport } from './importDialog.js';
import { toggleItemPane } from './itempane.js';
import { focusList, openColumnPicker } from './itemlist.js';
import { notesOpen, notesPanelButton } from './notes/notesView.js';
import { newNote, newFolder } from './notes/fileOps.js';
import { exportMenu } from './notes/exportNote.js';
import { ws } from './notes/workspace.js';
import { noteHooks } from './notes/hooks.js';
import { setFilter } from './notes/tree.js';
import { listHidden, peek } from './notes/panels.js';
import { pageOpen } from './pages.js';
import { openSettings } from './settingsView.js';

/* ---------------------------------------------------------------- toolbar */
let searchInput: HTMLInputElement, modeBtn: HTMLElement;

export function initToolbar() {
  const tb = document.getElementById('toolbar')!;
  tb.innerHTML = `
    <button class="icon-btn side-toggle" data-a="sidebar" title="Collections" aria-label="Show collections">${icon('menu')}</button>
    <div class="brand"><img class="brand-logo" src="/assets/favicon.svg?v=3" alt="" width="24" height="24"><span class="brand-name">Synapse</span></div>
    <button class="icon-btn" data-a="new" data-library title="New Item (${modLabel}N)">${icon('plus')}</button>
    <button class="icon-btn" data-a="identifier" data-library title="Add by DOI, arXiv ID or ISBN (${modLabel}⇧N)">${icon('wand')}</button>
    <span class="toolbar-sep" data-library></span>
    <button class="icon-btn" data-a="import" data-library title="Import BibTeX, RIS, EndNote… (${modLabel}I)">${icon('import')}</button>
    <button class="icon-btn" data-a="export" data-library title="Export (${modLabel}E)">${icon('export')}</button>
    <button class="icon-btn" data-a="new-note" data-notes hidden title="New note (${modLabel}N)">${icon('plus')}</button>
    <button class="icon-btn" data-a="new-folder" data-notes hidden title="New notes folder">${icon('folderPlus')}</button>
    <button class="icon-btn" data-a="note-export" data-notes hidden title="Export note as PDF, HTML or Markdown">${icon('export')}</button>
    <span class="spacer"></span>
    <div class="search notes-filter" data-notes hidden role="search">
      ${icon('search')}<input type="search" placeholder="Search notes" aria-label="Search notes" spellcheck="false">
      <button class="search-clear" data-a="clear-notes-search" title="Clear notes search" hidden>${icon('close', 'icon-sm')}</button>
    </div>
    <div class="search library-search" role="search" data-library>
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

  tb.addEventListener('click', (e) => {
    const target = e.target as Element;
    const b = target.closest<HTMLElement>('[data-a]');
    if (!b) return;
    const a = b.dataset.a!;
    if (notesOpen() && (a === 'columns' || a === 'pane')) { notesPanelButton(a); return; }   // the notes' own panels
    if (tasksPage.isOpen() && (a === 'columns' || a === 'pane')) { tasksPage.panelButton(a); return; }
    if (pageOpen() && (a === 'columns' || a === 'pane')) return;   // a page has no list or item pane
    if (a === 'new-note') newNote();
    if (a === 'new-folder') newFolder();
    if (a === 'note-export' && ws.session) exportMenu(ws.session.path, b);
    if (a === 'clear-notes-search') { notesSearch.value = ''; notesSearch.dispatchEvent(new Event('input')); notesSearch.focus(); }
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

  const notesSearch = tb.querySelector<HTMLInputElement>('.notes-filter input')!;
  const revealNotes = () => {
    noteHooks.graph?.closeGlobal();
    if (listHidden()) peek(true);
    document.getElementById('notes-view')?.classList.add('mobile-tree-open');
  };
  notesSearch.addEventListener('focus', revealNotes);
  notesSearch.addEventListener('input', () => {
    setFilter(notesSearch.value.trim().toLowerCase());
    tb.querySelector<HTMLElement>('[data-a="clear-notes-search"]')!.hidden = !notesSearch.value;
    revealNotes();
  });
  notesSearch.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); notesSearch.value = ''; notesSearch.dispatchEvent(new Event('input')); }
  });
  const run = debounce(() => { if (!document.body.dataset.workspace) A.setSearch(searchInput.value.trim()); }, 220);
  searchInput.addEventListener('input', () => {
    tb.querySelector<HTMLElement>('[data-a="clear-search"]')!.hidden = !searchInput.value;
    tb.querySelector<HTMLElement>('[data-a="save-search"]')!.hidden = !searchInput.value.trim();
    run();
  });
  on('workspace', () => {
    run.cancel(); searchInput.value = state.query;
    tb.querySelector<HTMLElement>('[data-a="clear-search"]')!.hidden = !state.query;
    tb.querySelector<HTMLElement>('[data-a="save-search"]')!.hidden = !state.query;
    closeMenu(); paintToolbar();
  });
  on('tasks-page', paintToolbar);
  on('note-status', paintToolbar);
  paintToolbar();
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); if (searchInput.value) clearSearch(); else focusList(); }
    if (e.key === 'Enter') { run.flush(); focusList(); }
    if (e.key === 'ArrowDown') { e.preventDefault(); focusList(); }
  });
}

/** Phone toolbars keep frequent actions visible and put the rest in one finger-sized menu. */
function openMobileMenu(anchor: HTMLElement): void {
  const notes = notesOpen(), library = !document.body.dataset.workspace;
  openMenu([
    ...(library ? [
      { label: 'New Item', icon: 'plus', run: () => A.newItem() },
      { label: 'Add by Identifier…', icon: 'wand', run: openIdentifier },
      { label: 'Import…', icon: 'import', run: () => openImport() },
      { label: 'Export…', icon: 'export', run: exportSelectionOrAll },
    ] : notes ? [
      { label: 'New Note…', icon: 'plus', run: () => newNote() },
      { label: 'New Notes Folder…', icon: 'folderPlus', run: () => newFolder() },
      { label: 'Export Note…', icon: 'export', disabled: !ws.session, run: () => { if (ws.session) exportMenu(ws.session.path, anchor); } },
    ] : []),
    'sep',
    !pageOpen() && { label: notesOpen() ? 'Notes List' : tasksPage.isOpen() ? 'Task Views' : 'Choose Columns', icon: 'columns',
      run: () => notesOpen() ? notesPanelButton('columns') : tasksPage.isOpen() ? tasksPage.panelButton('columns') : openColumnPicker(anchor) },
    !pageOpen() && { label: notesOpen() ? 'Note Details' : tasksPage.isOpen() ? 'Task Details' : 'Item Details', icon: 'sidebar',
      run: () => notesOpen() ? notesPanelButton('pane') : tasksPage.isOpen() ? tasksPage.panelButton('pane') : toggleItemPane() },
    'sep',
    { label: 'Settings', icon: 'gear', run: () => openSettings() },
    // the desktop layout on a phone (chosen there): the way back to the phone's own
    phoneScreen() && { label: 'Use the Phone Layout', icon: 'tablet', run: () => switchLayout('phone') },
  ], anchor);
}

export function openIdentifier() {
  if (state.view !== 'library' && !state.view.startsWith('folder:')) A.setView('library');
  openIdentifierDialog();
}

export function exportSelectionOrAll() {
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

export function setMode(m: SearchMode): void {
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
    ...[['tag:name', 'Tagged with…'], ['folder:name', 'In folder (or subfolder)…'], ['year:2018-2022', 'Year or range (also >2015)'], ['added:14d', 'Added in the last 14 days (or added:2026-10)'],
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

export function clearSearch() {
  searchInput.value = '';
  document.querySelector<HTMLElement>('[data-a="clear-search"]')!.hidden = true;
  document.querySelector<HTMLElement>('[data-a="save-search"]')!.hidden = true;
  A.setSearch('');
}


export function focusLibrarySearch(): void { searchInput.focus(); searchInput.select(); }

function paintToolbar(): void {
  const tb = document.getElementById('toolbar')!;
  const notes = notesOpen(), library = !document.body.dataset.workspace;
  tb.querySelectorAll<HTMLElement>('[data-library]').forEach((el) => { el.hidden = !library; });
  tb.querySelectorAll<HTMLElement>('[data-notes]').forEach((el) => { el.hidden = !notes; });
  tb.querySelector<HTMLButtonElement>('[data-a="note-export"]')!.disabled = !ws.session;
  for (const a of ['columns', 'pane']) tb.querySelector<HTMLElement>(`[data-a="${a}"]`)!.hidden = pageOpen();
}
