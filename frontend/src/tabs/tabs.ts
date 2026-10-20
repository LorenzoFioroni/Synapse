// The app's own tabs: the Synapse tab (the library, Notes and Tasks) and one tab per open PDF, each
// holding a viewer page (viewer.html) in a frame. They work the same in a browser tab, in the app
// window (python server.py --window) and on an iPad. The bar appears once a PDF is open in a tab.
//
// The tabs and their order live in model.ts (no DOM). A tab's viewer is loaded when the tab is
// first shown, so tabs restored from the last visit cost nothing until they are looked at. A hidden
// tab's frame keeps its size (visibility, not display), so its viewer keeps its layout and place.
import { type InAppPdf, setPdfTabOpener } from '../api.js';
import { icon } from '../icons.js';
import { emit, state } from '../store.js';
import { closeMenu, openMenu } from '../ui/menu.js';
import { debounce, escapeHtml, isMac, storage, store } from '../util.js';
import { HOME, type PdfTab, TabSet, reopenUrl, urlAtSpot } from './model.js';

const SAVE_KEY = 'zc.tabs';

let set = new TabSet();
let bar: HTMLElement;
let views: HTMLElement;
const frames = new Map<string, HTMLIFrameElement>();
let homeLabel = 'Library';
let homeTitle = document.title;
let homeFocus: Element | null = null;
let shown = HOME;   // the tab on screen
let onHome: () => void = () => {};

/** The key that goes with the tab keys: Control on a Mac, Alt elsewhere, where the browser keeps
 *  Control+W, Control+1… for its own tabs. Command+W always closes the whole window. */
const tabMod = (e: KeyboardEvent) => (isMac ? e.ctrlKey && !e.metaKey && !e.altKey : e.altKey && !e.ctrlKey && !e.metaKey);
export const tabModLabel = isMac ? '⌃' : 'Alt+';

export function initTabs(o: { onHome?: () => void } = {}): void {
  onHome = o.onHome || onHome;
  bar = document.getElementById('tabbar')!;
  views = document.getElementById('tab-views')!;
  set = TabSet.restore(storage<unknown>(SAVE_KEY, null));
  setPdfTabOpener({ enabled: () => state.settings.pdf_open_in !== 'browser', open });
  window.zcTabs = { shortcut: tabShortcut, touched: () => closeMenu(), list };

  bar.addEventListener('click', (e) => {
    const t = e.target as Element;
    const tab = t.closest<HTMLElement>('.tab');
    if (!tab) return;
    if (t.closest('.tab-close')) { closeTab(tab.dataset.id!); return; }
    show(tab.dataset.id!);
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

/** The Synapse tab's name: the workspace it shows (Library, Notes, Tasks). */
export function setHomeLabel(label: string): void {
  homeLabel = label;
  homeTitle = `${label} — Synapse`;
  if (shown === HOME) document.title = homeTitle;
  const el = bar?.querySelector('.tab.home .tab-title');
  if (el) el.textContent = label;
}

/** Show the Synapse tab (going to an item, a note, Back/Forward…). */
export function showHome(): void {
  if (set.active !== HOME) show(HOME);
}

export const pdfTabActive = (): boolean => set.active !== HOME;

/* ------------------------------------------------------------- opening */
function open(pdf: InAppPdf): void {
  const existing = !pdf.newTab && set.byKey(pdf.key);
  if (!existing) {
    const tab = set.add({ key: pdf.key, url: pdf.url, title: pdf.title });
    paint();
    show(tab.id);
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

/* -------------------------------------------------------- showing a tab */
function show(id: string): void {
  if (shown === HOME && id !== HOME) homeFocus = document.activeElement;
  set.activate(id);
  shown = set.active;
  const tab = set.get(set.active);
  for (const [fid, f] of frames) f.classList.toggle('active', fid === set.active);
  views.hidden = !tab;
  for (const sel of ['#toolbar', '#app', '#statusbar']) {
    const el = document.querySelector<HTMLElement>(sel);
    if (el) el.inert = !!tab;   // nothing behind the PDF takes the keyboard or the pointer
  }
  document.body.classList.toggle('pdf-tab-active', !!tab);
  if (tab) {
    const f = frameFor(tab);
    document.title = `${tab.title} — Synapse`;
    f.focus();
    f.contentWindow?.focus();
  } else {
    document.title = homeTitle;
    if (homeFocus instanceof HTMLElement && homeFocus.isConnected && homeFocus !== document.body) homeFocus.focus();
    else onHome();
    homeFocus = null;
  }
  closeMenu();
  paint();
  saveSoon();
}

function frameFor(tab: PdfTab): HTMLIFrameElement {
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

function closeTab(id: string): void {
  if (id === HOME) return;
  const wasActive = set.active === id;
  const tab = set.get(id);
  if (tab) tab.url = currentUrl(tab);   // reopened where it was left
  if (!set.close(id)) return;
  frames.get(id)?.remove();
  frames.delete(id);
  if (wasActive) show(set.active);
  else { paint(); saveSoon(); }
}

function closeMany(fn: () => void): void {
  for (const t of set.tabs) t.url = currentUrl(t);
  const before = new Set(set.tabs.map((t) => t.id));
  const active = set.active;
  fn();
  for (const id of before) if (!set.get(id)) { frames.get(id)?.remove(); frames.delete(id); }
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
    <div class="tab${t.id === set.active ? ' active' : ''}" data-id="${t.id}" role="tab" aria-selected="${t.id === set.active}" draggable="true" title="${escapeHtml(t.title)}">
      ${icon('pdf', 'icon-sm')}<span class="tab-title">${escapeHtml(t.title)}</span>
      <button class="tab-close" title="Close Tab (${tabModLabel}W)" aria-label="Close ${escapeHtml(t.title)}">${icon('close', 'icon-sm')}</button></div>`).join('')}`;
  bar.setAttribute('role', 'tablist');
  bar.querySelector('.tab.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function list() {
  return [{ id: HOME, title: homeLabel, url: '', active: set.active === HOME, loaded: true },
    ...set.tabs.map((t) => ({ id: t.id, title: t.title, url: currentUrl(t), active: t.id === set.active, loaded: frames.has(t.id) }))];
}

/* --------------------------------------------------------------- menus */
function tabMenu(id: string, at: { x: number; y: number }): void {
  const tab = set.get(id);
  const right = tab ? set.tabs.slice(set.tabs.indexOf(tab) + 1).length : 0;
  openMenu([
    tab && { label: 'Close Tab', icon: 'close', shortcut: `${tabModLabel}W`, run: () => closeTab(id) },
    tab && { label: 'Close Other Tabs', disabled: set.tabs.length < 2, run: () => closeMany(() => set.closeOthers(id)) },
    tab && { label: 'Close Tabs to the Right', disabled: !right, run: () => closeMany(() => set.closeRight(id)) },
    !tab && { label: 'Close All PDF Tabs', disabled: !set.tabs.length, run: () => closeMany(() => set.closeAll()) },
    { label: 'Reopen Closed Tab', icon: 'restore', shortcut: `${tabModLabel}⇧T`, disabled: !set.closed.length, run: reopen },
    tab && 'sep',
    tab && { label: 'Show in Library', icon: 'library', run: () => showInLibrary(tab) },
    tab && { label: 'Reload', run: () => reload(tab) },
    tab && { label: 'Open in a Browser Tab', icon: 'open', run: () => { window.open(currentUrl(tab), '_blank'); closeTab(id); } },
  ].filter((e) => !!e) as Parameters<typeof openMenu>[0], at);
}

function reopen(): void {
  const t = set.reopen();
  if (!t) return;
  show(t.id);
}

function reload(tab: PdfTab): void {
  const f = frames.get(tab.id);
  if (!f) return;
  tab.url = currentUrl(tab);
  f.src = tab.url;
}

function showInLibrary(tab: PdfTab): void {
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

/* ------------------------------------------------------ reordering tabs */
function initDragging(): void {
  let dragged: string | null = null;
  bar.addEventListener('dragstart', (e) => {
    const tab = (e.target as Element).closest<HTMLElement>('.tab:not(.home)');
    if (!tab) return;
    dragged = tab.dataset.id!;
    e.dataTransfer!.effectAllowed = 'move';
    e.dataTransfer!.setData('text/plain', tab.title);
    tab.classList.add('dragging');
  });
  const target = (e: DragEvent) => {
    const tabs = [...bar.querySelectorAll<HTMLElement>('.tab:not(.home)')];
    let index = tabs.length;
    for (const [i, t] of tabs.entries()) {
      const r = t.getBoundingClientRect();
      if (e.clientX < r.left + r.width / 2) { index = i; break; }
    }
    return { tabs, index };
  };
  bar.addEventListener('dragover', (e) => {
    if (!dragged) return;
    e.preventDefault();
    const { tabs, index } = target(e);
    tabs.forEach((t, i) => { t.classList.toggle('drop-before', i === index); t.classList.toggle('drop-after', index === tabs.length && i === tabs.length - 1); });
  });
  bar.addEventListener('drop', (e) => {
    if (!dragged) return;
    e.preventDefault();
    const { index } = target(e);
    const from = set.tabs.findIndex((t) => t.id === dragged);
    set.move(dragged, from >= 0 && from < index ? index - 1 : index);
    dragged = null;
    paint();
    saveSoon();
  });
  bar.addEventListener('dragend', () => {
    dragged = null;
    bar.querySelectorAll('.dragging, .drop-before, .drop-after').forEach((t) => t.classList.remove('dragging', 'drop-before', 'drop-after'));
  });
}

/* -------------------------------------------- remembered between visits */
/** A tab's address as it is now: the page being read, so it reopens there. */
function currentUrl(tab: PdfTab): string {
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
  store(SAVE_KEY, set.save(currentUrl));
}
const saveSoon = debounce(save, 400);

