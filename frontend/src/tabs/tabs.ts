// The app's own tabs. The first tab shows the library or Tasks (whichever the switch in the toolbar
// last chose); after it come note tabs and PDF tabs. They work the same in a browser tab, in the
// app window (python server.py --window) and on an iPad. The bar appears once a note or a PDF is
// open in a tab.
//
// - A note tab shows its note in the notes workspace, which is part of this page: choosing the tab
//   has the workspace open that note (`TabsHost.note`, from main.ts). Notes open VS Code's way, in
//   a preview tab the next note replaces until it is kept (model.ts).
// - A PDF tab holds a viewer page (viewer.html) in a frame over the page. Its viewer is loaded when
//   the tab is first shown, so tabs brought back by a reload cost nothing until they are looked at.
//   A hidden tab's frame keeps its size (visibility, not display), so its viewer keeps its layout
//   and place.
//
// Each window has its own tabs. They are kept in the window's session storage, so a reload brings
// them back, a new window starts with only the first tab, and a window opened after the app was
// closed starts afresh with the library. A tab moves to another window by dragging it there (or to
// a new window by dropping it outside every window), or with Move to New Window.
import { type InAppPdf, setPdfTabOpener } from '../api.js';
import { icon } from '../icons.js';
import { emit, state } from '../store.js';
import { closeMenu, openMenu } from '../ui/menu.js';
import { toast } from '../ui/toast.js';
import { debounce, escapeHtml, isMac } from '../util.js';
import { openNewWindow, readPlace } from '../windows.js';
import { HOME, type Tab, type TabSpec, TabSet, reopenUrl, urlAtSpot, validSpec } from './model.js';

const SAVE_KEY = 'zc.tabs';
const DRAG_TYPE = 'application/x-synapse-tab';
/** This window, among the windows a tab can be dragged between. */
const WIN = Math.random().toString(36).slice(2);

/** What main.ts does for the tabs: the first tab and note tabs show parts of this page. */
export interface TabsHost {
  /** The first tab was chosen: show the library or Tasks, whichever it shows. */
  home(): unknown;
  /** A note tab was chosen: show its note (null: Notes with nothing open) in the tab's mode.
   *  Resolves false when the note is gone (moved or deleted elsewhere); its tab then closes. */
  note(path: string | null, mode?: string): Promise<boolean>;
  /** No tab shows this note any more: save it and close it. */
  noteClosed(path: string): unknown;
  /** A note tab is going to another window: save its note now. */
  flushNote(path: string): unknown;
}

/** This window's session storage, which may be missing or full: then the tabs are simply not kept. */
const session = {
  get(): unknown {
    try { return JSON.parse(sessionStorage.getItem(SAVE_KEY) || 'null'); } catch { return null; }
  },
  set(v: unknown): void {
    try { sessionStorage.setItem(SAVE_KEY, JSON.stringify(v)); } catch { /* not kept */ }
  },
  clear(): void {
    try { sessionStorage.removeItem(SAVE_KEY); } catch { /* nothing kept */ }
  },
};

let set = new TabSet();
let host: TabsHost;
let bar: HTMLElement;
let views: HTMLElement;
const frames = new Map<string, HTMLIFrameElement>();
let homeLabel = 'Library';
let homeTitle = document.title;
/** The tab whose content is on the page under the PDF frames (the first tab or a note tab). */
let underneath: string | null = HOME;
/** The tab on screen (set.active may already name a tab just added, not displayed yet). */
let onScreen: string = HOME;
/** Where the keyboard was on the page when a PDF tab was shown, to give it back. */
let focusBefore: { id: string; el: Element | null } | null = null;

/** The key that goes with the tab keys: Control on a Mac, Alt elsewhere, where the browser keeps
 *  Control+W, Control+1… for its own tabs. Command+W always closes the whole window. */
const tabMod = (e: KeyboardEvent) => (isMac ? e.ctrlKey && !e.metaKey && !e.altKey : e.altKey && !e.ctrlKey && !e.metaKey);
export const tabModLabel = isMac ? '⌃' : 'Alt+';

export function initTabs(h: TabsHost): void {
  host = h;
  bar = document.getElementById('tabbar')!;
  views = document.getElementById('tab-views')!;
  // A window opened by "New Window" starts with no tabs, even where the browser copied this
  // window's session storage into it.
  if (readPlace(location.hash)?.fresh) session.clear();
  set = TabSet.restore(session.get());
  try { localStorage.removeItem(SAVE_KEY); } catch { /* the tabs of all windows were once kept here */ }
  setPdfTabOpener({ enabled: () => state.settings.pdf_open_in !== 'browser', open });
  window.zcTabs = { shortcut: tabShortcut, touched: () => closeMenu(), list, win: WIN };

  bar.addEventListener('click', (e) => {
    const t = e.target as Element;
    const tab = t.closest<HTMLElement>('.tab');
    if (!tab) return;
    if (t.closest('.tab-close')) { closeTab(tab.dataset.id!); return; }
    show(tab.dataset.id!);
  });
  // A double-click keeps a preview tab. Counted by hand: the first click redraws the bar, so the
  // browser would not see the second click on the same element.
  let lastPress = { id: '', at: 0 };
  bar.addEventListener('pointerdown', (e) => {
    const tab = (e.target as Element).closest<HTMLElement>('.tab:not(.home)');
    if (!tab || e.button !== 0 || (e.target as Element).closest('.tab-close')) return;
    const id = tab.dataset.id!, now = performance.now();
    if (lastPress.id === id && now - lastPress.at < 450 && set.keep(id)) { paint(); saveSoon(); }
    lastPress = { id, at: now };
  });
  // Middle click closes (mousedown too, or the browser starts scrolling).
  bar.addEventListener('mousedown', (e) => { if (e.button === 1 && (e.target as Element).closest('.tab:not(.home)')) e.preventDefault(); });
  bar.addEventListener('auxclick', (e) => {
    const tab = (e.target as Element).closest<HTMLElement>('.tab:not(.home)');
    if (tab && e.button === 1) { e.preventDefault(); closeTab(tab.dataset.id!); }
  });
  bar.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const tab = (e.target as Element).closest<HTMLElement>('.tab');
    tabMenu(tab?.dataset.id || HOME, { x: e.clientX, y: e.clientY });
  });
  initDragging();
  window.addEventListener('pagehide', save);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') save(); });

  paint();
  if (set.active !== HOME) show(set.active);
}

/** The first tab's name: what it shows (Library or Tasks). */
export function setHomeLabel(label: string): void {
  homeLabel = label;
  homeTitle = `${label} — Synapse`;
  if (set.active === HOME) document.title = homeTitle;
  const el = bar?.querySelector('.tab.home .tab-title');
  if (el) el.textContent = label;
}

/** The page shows the library or Tasks now (main.ts did it, e.g. going Back or to an item): the
 *  first tab is the one shown. */
export function showHome(): void {
  if (set.active === HOME && underneath === HOME) return;
  display(HOME);
  underneath = HOME;
  dropEmptyNotes();
  paint();
  saveSoon();
}

/** The page left Notes for the library or Tasks (main.ts): the first tab is what is underneath now,
 *  and the tab shown unless a PDF tab is. */
export function homeUnderneath(): void {
  if (!bar) return;
  underneath = HOME;
  if (set.get(set.active)?.kind === 'note') display(HOME);
  dropEmptyNotes();
  paint();
  saveSoon();
}

export const pdfTabActive = (): boolean => set.get(set.active)?.kind === 'pdf';

/** Open the viewer at `url` (a `/viewer.html?…` address) in a tab: a PDF moved from another window. */
export function openViewerTab(url: string): void {
  const spec = pdfSpec(url);
  if (!spec) return;
  set.add(spec);
  show(set.active);
}

function pdfSpec(url: string): TabSpec | null {
  if (!url.startsWith('/viewer.html?')) return null;
  const q = new URLSearchParams(url.split('?')[1]);
  const key = q.get('file');
  return key ? { kind: 'pdf', key, url, title: q.get('title') || 'PDF' } : null;
}

/* ------------------------------------------------------------- opening */
function open(pdf: InAppPdf): void {
  const existing = !pdf.newTab && set.byKey(pdf.key);
  if (!existing) {
    set.add({ key: pdf.key, url: pdf.url, title: pdf.title });
    show(set.active);
    return;
  }
  const hasSpot = !!pdf.spot.page;
  const frame = frames.get(existing.id);
  if (hasSpot && !frame) existing.url = urlAtSpot(existing.url, pdf.spot);
  show(existing.id);
  if (hasSpot && frame) goto(frame, pdf.spot);
}

/** Point a tab's viewer at a spot, once it has loaded. */
function goto(frame: HTMLIFrameElement, spot: InAppPdf['spot']): void {
  const run = () => frame.contentWindow?.zcViewerGoto?.({ page: spot.page, q: spot.q, rect: spot.rect });
  if (frame.contentWindow?.zcViewerGoto && frame.contentDocument?.readyState === 'complete') run();
  else frame.addEventListener('load', run, { once: true });
}

/**
 * Show a note in a tab (null: Notes with nothing open), VS Code's way (model.ts `openNote`): its
 * own tab, else the preview tab, else a new one. `keep`: the tab is kept. `last` (no note): the
 * note tab used last, if there is one. Resolves when the note is on screen.
 */
export async function openNoteTab(path: string | null, o: { keep?: boolean; last?: boolean } = {}): Promise<void> {
  const preview = set.previewTab();
  const replaced = preview?.key;
  const tab = set.openNote(path, o);
  await showNote(tab);
  // the preview tab now shows another note: the one it showed is closed (unless another tab has it)
  if (replaced && tab === preview && tab.key !== replaced && !set.byKey(replaced, 'note')) host.noteClosed(replaced);
}

/** The note shown was put in another mode: it is the tab's. */
export function noteModeChanged(mode: string): void {
  const t = set.get(set.active);
  if (t?.kind === 'note') { t.mode = mode; saveSoon(); }
}

/** The note shown was edited: a preview tab is kept from now on. */
export function noteEdited(): void {
  const t = set.get(set.active);
  if (t?.kind === 'note' && set.keep(t.id)) { paint(); saveSoon(); }
}

/** Notes were moved or renamed: their tabs follow. */
export function notesRenamed(rename: (path: string) => string): void {
  set.renameNotes(rename);
  paint();
  saveSoon();
}

/** Notes were deleted: their tabs close (the one shown becomes Notes with nothing open). */
export function notesDeleted(gone: (path: string) => boolean): void {
  set.notesGone(gone);
  const t = set.get(set.active);
  if (t?.kind === 'note') document.title = `${t.title} — Synapse`;
  paint();
  saveSoon();
}

/** No note tabs (Notes was turned off in Settings). */
export function closeNoteTabs(): void {
  closeMany(() => { for (const t of [...set.tabs]) if (t.kind === 'note') set.close(t.id); });
}

/* -------------------------------------------------------- showing a tab */
/** Put tab `id` on screen: its frame for a PDF tab, the page for the others. Nothing more. */
function display(id: string): void {
  const leaving = set.get(onScreen);
  set.activate(id);
  onScreen = set.active;
  const tab = set.get(set.active);
  const pdf = tab?.kind === 'pdf';
  if (pdf && leaving?.kind !== 'pdf') focusBefore = { id: underneath || HOME, el: document.activeElement };
  for (const [fid, f] of frames) f.classList.toggle('active', fid === set.active);
  views.hidden = !pdf;
  for (const sel of ['#toolbar', '#app', '#statusbar']) {
    const el = document.querySelector<HTMLElement>(sel);
    if (el) el.inert = pdf;   // nothing behind the PDF takes the keyboard or the pointer
  }
  document.body.classList.toggle('pdf-tab-active', pdf);
  document.title = tab ? `${tab.title} — Synapse` : homeTitle;
  closeMenu();
}

/** Show tab `id` as chosen by the reader (a click, a key, closing the tab before it). */
function show(id: string): void {
  display(id);
  const tab = set.get(set.active);
  if (tab?.kind === 'pdf') {
    const f = frameFor(tab);
    f.focus();
    f.contentWindow?.focus();
  } else if (underneath === set.active) {
    // back from a PDF to what is still on the page: give the keyboard back where it was
    const el = focusBefore?.id === set.active ? focusBefore.el : null;
    if (el instanceof HTMLElement && el.isConnected && el !== document.body) el.focus();
  } else if (tab) {
    showNote(tab);
  } else {
    underneath = HOME;
    dropEmptyNotes();
    host.home();
  }
  paint();
  saveSoon();
}

/** An empty Notes tab lasts only while Notes is on screen. */
function dropEmptyNotes(): void {
  for (const t of [...set.tabs]) if (t.kind === 'note' && !t.key && t.id !== set.active) { set.close(t.id); dropped(t); }
}

async function showNote(tab: Tab): Promise<void> {
  display(tab.id);
  underneath = tab.id;
  paint();
  saveSoon();
  const ok = await host.note(tab.key || null, tab.mode);
  if (!ok && set.get(tab.id)) closeTab(tab.id);
}

function frameFor(tab: Tab): HTMLIFrameElement {
  let f = frames.get(tab.id);
  if (f) return f;
  f = document.createElement('iframe');
  f.className = 'tab-frame active';
  f.title = tab.title;
  f.src = tab.url;
  frames.set(tab.id, f);
  views.appendChild(f);
  return f;
}

/** A tab is gone from this window: its frame, or its note if the page shows it. */
function dropped(tab: Tab): void {
  frames.get(tab.id)?.remove();
  frames.delete(tab.id);
  if (tab.kind !== 'note') return;
  if (underneath === tab.id) underneath = null;
  if (tab.key && !set.byKey(tab.key, 'note')) host.noteClosed(tab.key);
}

function closeTab(id: string): void {
  if (id === HOME) return;
  const wasActive = set.active === id;
  const tab = set.get(id);
  if (!tab) return;
  if (tab.kind === 'pdf') tab.url = currentUrl(tab);   // reopened where it was left
  set.close(id);
  dropped(tab);
  if (wasActive) show(set.active);
  else { paint(); saveSoon(); }
}

function closeMany(fn: () => void): void {
  for (const t of set.tabs) if (t.kind === 'pdf') t.url = currentUrl(t);
  const before = [...set.tabs];
  const active = set.active;
  fn();
  for (const t of before) if (!set.get(t.id)) dropped(t);
  if (set.active !== active) show(set.active); else { paint(); saveSoon(); }
}

/* ------------------------------------------------------------- drawing */
function paint(): void {
  const any = set.tabs.length > 0;
  bar.hidden = !any;
  document.body.classList.toggle('has-tabs', any);
  bar.innerHTML = `
    <button class="tab home${set.active === HOME ? ' active' : ''}" data-id="${HOME}" role="tab" aria-selected="${set.active === HOME}" title="${escapeHtml(homeLabel)}">
      <img class="tab-logo" src="/assets/favicon.svg?v=3" alt="" width="16" height="16"><span class="tab-title">${escapeHtml(homeLabel)}</span></button>
    ${set.tabs.map((t) => `
    <div class="tab ${t.kind}${t.id === set.active ? ' active' : ''}${t.preview ? ' preview' : ''}" data-id="${t.id}" role="tab"
         aria-selected="${t.id === set.active}" draggable="true" title="${escapeHtml(t.title)}${t.preview ? ' (preview: double-click to keep the tab)' : ''}">
      ${icon(t.kind === 'pdf' ? 'pdf' : 'doc', 'icon-sm')}<span class="tab-title">${escapeHtml(t.title)}</span>
      <button class="tab-close" title="Close Tab (${tabModLabel}W)" aria-label="Close ${escapeHtml(t.title)}">${icon('close', 'icon-sm')}</button></div>`).join('')}`;
  bar.setAttribute('role', 'tablist');
  bar.querySelector('.tab.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function list() {
  return [{ id: HOME, kind: 'home', title: homeLabel, url: '', key: '', active: set.active === HOME, loaded: true, preview: false },
    ...set.tabs.map((t) => ({ id: t.id, kind: t.kind, title: t.title, url: t.kind === 'pdf' ? currentUrl(t) : '', key: t.key,
      active: t.id === set.active, loaded: t.kind === 'note' || frames.has(t.id), preview: t.preview }))];
}

/* --------------------------------------------------------------- menus */
function tabMenu(id: string, at: { x: number; y: number }): void {
  const tab = set.get(id);
  const pdf = tab?.kind === 'pdf' ? tab : null;
  const right = tab ? set.tabs.slice(set.tabs.indexOf(tab) + 1).length : 0;
  openMenu([
    tab && { label: 'Close Tab', icon: 'close', shortcut: `${tabModLabel}W`, run: () => closeTab(id) },
    tab && { label: 'Close Other Tabs', disabled: set.tabs.length < 2, run: () => closeMany(() => set.closeOthers(id)) },
    tab && { label: 'Close Tabs to the Right', disabled: !right, run: () => closeMany(() => set.closeRight(id)) },
    !tab && { label: 'Close All Tabs', disabled: !set.tabs.length, run: () => closeMany(() => set.closeAll()) },
    { label: 'Reopen Closed Tab', icon: 'restore', shortcut: `${tabModLabel}⇧T`, disabled: !set.closed.length, run: reopen },
    tab?.preview && { label: 'Keep Tab Open', icon: 'check', run: () => { set.keep(id); paint(); saveSoon(); } },
    'sep',
    tab && { label: 'Move to New Window', icon: 'newWindow', disabled: tab.kind === 'note' && !tab.key, run: () => moveToNewWindow(tab) },
    !tab && { label: 'New Window', icon: 'newWindow', run: () => openNewWindow() },
    pdf && { label: 'Open in a Browser Tab', icon: 'open', run: () => { window.open(currentUrl(pdf), '_blank'); closeTab(id); } },
    pdf && 'sep',
    pdf && { label: 'Show in Library', icon: 'library', run: () => showInLibrary(pdf) },
    pdf && { label: 'Reload', icon: 'refresh', run: () => reload(pdf) },
  ].filter((e) => !!e) as Parameters<typeof openMenu>[0], at);
}

/** What opens a tab in a new window: its PDF at the page it is on, or its note. */
function placeOf(tab: Tab) {
  return tab.kind === 'pdf' ? { pdf: currentUrl(tab) } : { note: tab.key };
}

/** The tab goes to a new window of its own (beside a first tab). */
function moveToNewWindow(tab: Tab, at?: { left: number; top: number }): void {
  if (tab.kind === 'note' && !tab.key) return;
  if (tab.kind === 'note') host.flushNote(tab.key);
  openNewWindow(placeOf(tab), at);
  closeTab(tab.id);
}

function reopen(): void {
  const t = set.reopen();
  if (t) show(t.id);
}

function reload(tab: Tab): void {
  const f = frames.get(tab.id);
  if (!f) return;
  tab.url = currentUrl(tab);
  f.src = tab.url;
}

function showInLibrary(tab: Tab): void {
  const id = new URLSearchParams(tab.url.split('?')[1] || '').get('paper_id');
  showHome();
  if (id) emit('show-item', id);
}

/* ------------------------------------------------------------ the keys */
/** The tab keys: Control (Mac) or Alt (elsewhere) with W (close), ⇧T (reopen), 1–9 (go to);
 *  Control+Tab and Control+⇧Tab (next, previous; in a browser tab the browser keeps these). */
export function tabShortcut(e: KeyboardEvent): boolean {
  if (e.ctrlKey && !e.metaKey && !e.altKey && e.key === 'Tab') { cycle(e.shiftKey ? -1 : 1); return true; }
  if (e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && (e.key === 'PageDown' || e.key === 'PageUp')) {
    cycle(e.key === 'PageDown' ? 1 : -1);
    return true;
  }
  if (!tabMod(e)) return false;
  const k = e.code;
  if (k === 'KeyW' && !e.shiftKey) {
    if (set.active === HOME) return false;
    closeTab(set.active);
    return true;
  }
  if (k === 'KeyT' && e.shiftKey) { reopen(); return true; }
  const n = /^Digit([1-9])$/.exec(k);
  if (n && !e.shiftKey && set.tabs.length) {
    const id = set.nth(Number(n[1]));
    if (id) show(id);
    return true;
  }
  return false;
}

function cycle(dir: number): void {
  if (!set.tabs.length) return;
  show(set.step(dir));
}

/* ------------------------------------------------- dragging tabs around */
// A tab is dragged with its description (DRAG_TYPE). Dropped on this window's bar it changes
// place; dropped on another Synapse window (its bar or its page) that window opens it and says so
// on the 'zc-tabs' channel, and this one closes it; dropped outside every window it moves to a new
// window there. Browsers allow opening that window only shortly after the drag started (the
// mouse press counts as the click that opens it): after a long drag, a message says to use Move
// to New Window instead.
interface Dragged { win: string; id: string; spec: TabSpec }
const channel = 'BroadcastChannel' in window ? new BroadcastChannel('zc-tabs') : null;

function initDragging(): void {
  let dragging: { id: string; outside: boolean } | null = null;

  channel?.addEventListener('message', (e) => {
    const m = e.data || {};
    if (m.type === 'taken' && m.win === WIN && set.get(m.id)) closeTab(m.id);
  });

  bar.addEventListener('dragstart', (e) => {
    const el = (e.target as Element).closest<HTMLElement>('.tab:not(.home)');
    const tab = el && set.get(el.dataset.id!);
    if (!el || !tab || !e.dataTransfer) return;
    const spec: TabSpec = tab.kind === 'pdf'
      ? { kind: 'pdf', key: tab.key, url: currentUrl(tab), title: tab.title }
      : { kind: 'note', key: tab.key, title: tab.title };
    if (tab.kind === 'note' && tab.key) host.flushNote(tab.key);   // the other window reads it from the server
    dragging = { id: tab.id, outside: false };
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData(DRAG_TYPE, JSON.stringify({ win: WIN, id: tab.id, spec } satisfies Dragged));
    el.classList.add('dragging');
  });

  const ours = (e: DragEvent) => !!e.dataTransfer?.types.includes(DRAG_TYPE);
  /** Where in the bar a drop at `e` goes: an index among the tabs after the first. */
  const slot = (e: DragEvent) => {
    const tabs = [...bar.querySelectorAll<HTMLElement>('.tab:not(.home)')];
    let index = tabs.length;
    for (const [i, t] of tabs.entries()) {
      const r = t.getBoundingClientRect();
      if (e.clientX < r.left + r.width / 2) { index = i; break; }
    }
    return { tabs, index };
  };
  const unmark = () => bar.querySelectorAll('.drop-before, .drop-after').forEach((t) => t.classList.remove('drop-before', 'drop-after'));

  // The whole page takes a tab from another window; the bar also shows where it will go.
  window.addEventListener('dragover', (e) => {
    if (!ours(e)) return;
    e.preventDefault();
    e.dataTransfer!.dropEffect = 'move';
    if (dragging) dragging.outside = false;
    unmark();
    if (!bar.hidden && bar.contains(e.target as Node)) {
      const { tabs, index } = slot(e);
      tabs.forEach((t, i) => { t.classList.toggle('drop-before', i === index); t.classList.toggle('drop-after', index === tabs.length && i === tabs.length - 1); });
    }
  }, true);
  document.addEventListener('dragleave', (e) => {
    if (dragging && !e.relatedTarget) {   // left the page: maybe for another window, maybe for nowhere
      const out = e.clientX <= 0 || e.clientY <= 0 || e.clientX >= innerWidth || e.clientY >= innerHeight;
      if (out) { dragging.outside = true; unmark(); }
    }
  });
  window.addEventListener('drop', (e) => {
    if (!ours(e)) return;
    e.preventDefault();
    e.stopPropagation();
    unmark();
    let d: Dragged;
    try { d = JSON.parse(e.dataTransfer!.getData(DRAG_TYPE)); } catch { return; }
    const onBar = !bar.hidden && bar.contains(e.target as Node);
    const { index } = slot(e);
    if (d.win === WIN) {
      if (!onBar) return;
      const from = set.tabs.findIndex((t) => t.id === d.id);
      set.move(d.id, from >= 0 && from < index ? index - 1 : index);
      paint();
      saveSoon();
      return;
    }
    if (!validSpec(d.spec)) return;
    takeTab(d.spec, onBar ? index : set.tabs.length);
    channel?.postMessage({ type: 'taken', win: d.win, id: d.id });
  }, true);

  bar.addEventListener('dragend', (e) => {
    bar.querySelectorAll('.dragging').forEach((t) => t.classList.remove('dragging'));
    unmark();
    const d = dragging;
    dragging = null;
    if (!d || e.dataTransfer?.dropEffect === 'move') return;   // taken here or by another window
    const out = d.outside || e.clientX < 0 || e.clientY < 0 || e.clientX > innerWidth || e.clientY > innerHeight;
    if (!out) return;
    // A window that took it says so on the channel (and the tab closes): give it a moment first.
    setTimeout(() => {
      const tab = set.get(d.id);
      if (!tab) return;
      const activation = (navigator as Navigator & { userActivation?: { isActive: boolean } }).userActivation;
      if (activation && !activation.isActive) {
        toast('To move the tab to a new window, drop it outside the window a little sooner, or right-click it: Move to New Window.');
        return;
      }
      moveToNewWindow(tab, { left: e.screenX - 120, top: e.screenY - 20 });
    }, 250);
  });
}

/** A tab dragged in from another window, put at `index`. */
function takeTab(spec: TabSpec, index: number): void {
  if (spec.kind === 'note') {
    if (!spec.key || state.settings.notes_enabled === false) return;
    const own = set.byKey(spec.key, 'note');
    const tab = own || set.add({ kind: 'note', key: spec.key }, { at: index, activate: false });
    tab.preview = false;
    showNote(tab);
    return;
  }
  set.add(spec, { at: index });
  show(set.active);
}

/* -------------------------------------------- kept for a reload of the window */
/** A PDF tab's address as it is now: the page being read, so it reopens there. */
function currentUrl(tab: Tab): string {
  const f = frames.get(tab.id);
  try {
    const w = f?.contentWindow;
    if (!w || !w.location.pathname.endsWith('/viewer.html')) return tab.url;
    const pos = (w.zcLinks?.currentPosition as (() => { page?: number } | null) | undefined)?.();
    return reopenUrl(w.location.pathname + w.location.search, pos?.page);
  } catch {
    return tab.url;
  }
}

function save(): void {
  session.set(set.save(currentUrl));
}
const saveSoon = debounce(save, 400);
