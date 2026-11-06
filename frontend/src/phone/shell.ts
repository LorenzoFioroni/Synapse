// The phone layout's frame: a tab bar (Library, Notes, "+", one plugin destination the reader
// pins, and Plugins for the rest) and, per tab, a stack of screens. A screen pushed on top gets a
// back button; the phone's Back gesture, the browser's Back button and a swipe from the left edge
// go back too. Plugins come and go while the page runs, so the tab bar is drawn again whenever
// their lists, pages or badges change. See docs/phone.md.
import { badges, pages, type Cleanup } from '../extend.js';
import { icon } from '../icons.js';
import { on, state } from '../store.js';
import { openMenu } from '../ui/menu.js';
import { escapeHtml as esc, storage, store } from '../util.js';
import { sourceBadges } from '../ui/sourceBadges.js';
import { type Destination, type TabId, destinations, hubBadge, hubMarks, pinned, tabSlots } from './model.js';

/** A screen: what its back button is called on the screen above it, and how it draws itself. */
export interface Screen {
  title: string;
  /** Identifies the screen (to bring an open one forward instead of opening it twice). */
  key?: string;
  mount(el: HTMLElement, ctx: ScreenCtx): Cleanup | Promise<Cleanup>;
}

export interface ScreenCtx {
  /** The title of the screen below (for the back button); '' on a tab's first screen. */
  backLabel: string;
  /** Called before the screen goes away (it may save something first). */
  onLeave(fn: () => unknown): void;
}

interface Live { screen: Screen; el: HTMLElement; cleanup: (() => void) | null; leave: (() => unknown)[] }

type Tab = Exclude<TabId, 'add'>;
const TABS: Tab[] = ['library', 'notes', 'pin', 'plugins'];
const PIN_KEY = 'zc.phone.pin';

let root: HTMLElement;
let bar: HTMLElement;
const views = {} as Record<Tab, HTMLElement>;
const stacks = {} as Record<Tab, Live[]>;
let active: Tab = 'library';
let roots: Record<Tab, () => Screen | null>;
let onAdd: () => void = () => {};
let pinId: string | null = storage<string | null>(PIN_KEY, null);
let pin: Destination | null = null;
let dests: Destination[] = [];

export const currentTab = (): Tab => active;
export const destinationList = (): Destination[] => dests;
export const pinnedDestination = (): Destination | null => pin;

/** Build the frame. ``rootOf`` gives each tab's first screen (null: the tab is not there). */
export function initShell(rootOf: Record<Tab, () => Screen | null>, add: () => void): void {
  roots = rootOf;
  onAdd = add;
  root = document.createElement('div');
  root.id = 'phone';
  root.innerHTML = `<div class="ph-views">${TABS.map((t) => `<div class="ph-tab-view" data-tab="${t}" hidden></div>`).join('')}</div>
    <nav class="ph-tabbar" aria-label="Sections"></nav>`;
  document.body.appendChild(root);
  for (const t of TABS) { views[t] = root.querySelector<HTMLElement>(`[data-tab="${t}"]`)!; stacks[t] = []; }
  bar = root.querySelector<HTMLElement>('.ph-tabbar')!;
  bar.addEventListener('click', (e) => {
    const b = (e.target as Element).closest<HTMLElement>('[data-slot]');
    if (!b) return;
    const slot = b.dataset.slot as TabId;
    if (slot === 'add') { onAdd(); return; }
    selectTab(slot as Tab);
  });
  // long press (or right-click) on the pinned tab: choose what it shows
  bar.addEventListener('contextmenu', (e) => {
    if (!(e.target as Element).closest('[data-slot="pin"], [data-slot="plugins"]')) return;
    e.preventDefault();
    choosePin();
  });
  root.addEventListener('click', (e) => {
    if ((e.target as Element).closest('[data-ph="back"]')) { e.preventDefault(); back(); }
  });
  initEdgeSwipe();
  window.addEventListener('popstate', (e) => {
    const depth = (e.state as { phone?: number } | null)?.phone ?? 0;
    const stack = stacks[active];
    if (stack.length - 1 > depth) void popTo(depth + 1);
  });
  history.replaceState({ ...(history.state || {}), phone: 0 }, '');
  const repaint = () => paintTabs();
  on('sources', repaint);
  on('settings', repaint);
  pages.subscribe(repaint);
  paintTabs();
  selectTab('library');
}

/** The plugin lists and pages, the pinned one and the tab bar, as they are now. */
export function paintTabs(): void {
  const before = pin?.id ?? null;
  dests = destinations(state.sources, pages.all(), badges);
  pin = pinned(dests, pinId);
  const notes = state.settings.notes_enabled !== false;
  const slots = tabSlots(dests, pin, { notes });
  const label: Record<TabId, string> = { library: 'Library', notes: 'Notes', add: 'Add', pin: pin?.title || '', plugins: 'Plugins' };
  const iconOf: Record<TabId, string> = { library: 'library', notes: 'edit', add: 'plus', pin: pin?.icon || 'plugin', plugins: 'plugin' };
  const badgeOf = (t: TabId) => (t === 'pin' ? pin?.badge : t === 'plugins' ? hubBadge(dests, pin) : null) || '';
  // today's Following / Cites you … as on the desktop sidebar, on the pinned tab and on Plugins
  const marksOf = (t: TabId) => sourceBadges(t === 'pin' ? (pin?.marks || []).slice(0, 3) : t === 'plugins' ? hubMarks(dests, pin) : [], true);
  bar.innerHTML = slots.map((t) => (t === 'add'
    ? `<button class="ph-add" data-slot="add" aria-label="Add">${icon('plus')}</button>`
    : `<button class="ph-tab${t === active ? ' on' : ''}" data-slot="${t}"${t === active ? ' aria-current="page"' : ''}>
        <span class="ph-tab-icon">${icon(iconOf[t])}${badgeOf(t) ? `<span class="ph-tab-badge">${esc(badgeOf(t).slice(0, 4))}</span>` : ''}${marksOf(t) ? `<span class="ph-tab-marks">${marksOf(t)}</span>` : ''}</span>
        <span class="ph-tab-label">${esc(shortLabel(label[t]))}</span></button>`)).join('');
  bar.dataset.slots = String(slots.length);
  // what the pinned tab shows changed (chosen elsewhere, or its plugin stopped): start it afresh
  if ((pin?.id ?? null) !== before && stacks.pin) {
    resetTab('pin');
    if (active === 'pin' && !pin) selectTab('plugins');
  }
  if (!slots.includes(active) && stacks.library) selectTab('library');
}

/** Tab labels stay short: a plugin's list title loses words after the first few letters. */
function shortLabel(s: string): string {
  if (s.length <= 10) return s;
  const first = s.split(/[\s:]+/).find((w) => w.length > 1) || s;
  return first.length <= 10 ? first : `${first.slice(0, 9)}…`;
}

/** Show ``tab``; choosing the tab shown again goes back to its first screen. */
export function selectTab(tab: Tab): void {
  if (tab === active && stacks[tab].length) {
    if (stacks[tab].length > 1) void popTo(1);
    else stacks[tab][0].el.querySelector<HTMLElement>('.ph-scroll')?.scrollTo({ top: 0, behavior: 'smooth' });
    return;
  }
  active = tab;
  for (const t of TABS) views[t].hidden = t !== tab;
  if (!stacks[tab].length) {
    const first = roots[tab]();
    if (first) void open(tab, first);
  }
  document.body.dataset.phoneTab = tab;
  bar.querySelectorAll<HTMLElement>('[data-slot]').forEach((b) => {
    const on = b.dataset.slot === tab;
    b.classList.toggle('on', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
}

/** Start ``tab`` again from its first screen (when what it shows changed). */
export function resetTab(tab: Tab): void {
  for (const live of stacks[tab].splice(0)) close(live);
  views[tab].replaceChildren();
  if (tab === active) {
    const first = roots[tab]();
    if (first) void open(tab, first);
  }
}

async function open(tab: Tab, screen: Screen): Promise<void> {
  const stack = stacks[tab];
  const el = document.createElement('section');
  el.className = 'ph-screen';
  if (screen.key) el.dataset.screen = screen.key;
  const live: Live = { screen, el, cleanup: null, leave: [] };
  const under = stack[stack.length - 1];
  stack.push(live);
  views[tab].appendChild(el);
  if (under) { under.el.hidden = true; el.classList.add('entering'); requestAnimationFrame(() => el.classList.remove('entering')); }
  try {
    const r = await screen.mount(el, { backLabel: under?.screen.title || '', onLeave: (fn) => live.leave.push(fn) });
    if (typeof r === 'function') {
      if (stack.includes(live)) live.cleanup = r; else r();
    }
  } catch (e) {
    console.error(e);
    el.innerHTML = `${under ? '' : ''}<div class="ph-empty">${icon('alert')}<h3>This could not be shown</h3><p>${esc((e as Error).message)}</p>
      ${under ? '<button class="ph-pill" data-ph="back">Back</button>' : ''}</div>`;
  }
}

function close(live: Live): void {
  for (const fn of live.leave) { try { void fn(); } catch (e) { console.error(e); } }
  try { live.cleanup?.(); } catch (e) { console.error(e); }
  live.el.remove();
}

/** Put ``screen`` on top of the tab shown (or of ``tab``, which is then shown). */
export function push(screen: Screen, tab: Tab = active): void {
  if (tab !== active) selectTab(tab);
  const stack = stacks[tab];
  const same = screen.key ? stack.findIndex((l) => l.screen.key === screen.key) : -1;
  if (same >= 0 && same === stack.length - 1) return;   // already on top
  history.pushState({ ...(history.state || {}), phone: stack.length }, '');
  void open(tab, screen);
}

/** Go back one screen (through the browser history, so its Back button agrees). */
export function back(): void {
  if (stacks[active].length > 1) history.back();
}

/** Keep the ``depth`` lowest screens of the tab shown. */
async function popTo(depth: number): Promise<void> {
  const stack = stacks[active];
  while (stack.length > Math.max(1, depth)) {
    const live = stack.pop()!;
    close(live);
  }
  const top = stack[stack.length - 1];
  if (top) top.el.hidden = false;
}

/** Show ``tab`` with ``screen`` right above its first one (a plugin's list opened from elsewhere). */
export function showOn(tab: Tab, screen: Screen | null): void {
  selectTab(tab);
  void popTo(1).then(() => { if (screen) push(screen, tab); });
}

/** A swipe from the left edge of the screen goes back, as in other phone apps. */
function initEdgeSwipe(): void {
  let x0 = -1, y0 = 0, el: HTMLElement | null = null;
  root.addEventListener('touchstart', (e) => {
    const t = e.touches[0];
    if (t.clientX > 22 || stacks[active].length < 2 || e.touches.length > 1) { x0 = -1; return; }
    x0 = t.clientX; y0 = t.clientY;
    el = stacks[active][stacks[active].length - 1].el;
  }, { passive: true });
  root.addEventListener('touchmove', (e) => {
    if (x0 < 0 || !el) return;
    const dx = e.touches[0].clientX - x0, dy = Math.abs(e.touches[0].clientY - y0);
    if (dy > 40 && dx < 30) { x0 = -1; el.style.transform = ''; return; }
    el.style.transform = `translateX(${Math.max(0, dx)}px)`;
  }, { passive: true });
  root.addEventListener('touchend', (e) => {
    if (x0 < 0 || !el) return;
    const dx = (e.changedTouches[0]?.clientX ?? x0) - x0;
    const target = el;
    x0 = -1; el = null;
    target.style.transform = '';
    if (dx > 90) back();
  });
}

/** The sheet that picks the destination pinned next to Notes. */
export function choosePin(): void {
  const usable = dests.filter((d) => d.phone);
  if (!usable.length) return;
  openMenu([
    { section: 'Tab next to Notes' },
    ...usable.map((d) => ({ label: d.title, checked: d.id === pin?.id, run: () => setPin(d.id) })),
    { label: 'None (all in Plugins)', checked: !pin, run: () => setPin('none') },
  ], { x: 0, y: innerHeight });
}

export function setPin(id: string): void {
  pinId = id;
  store(PIN_KEY, id);
  paintTabs();
}

/** The destination ``id`` ("source:…" or "page:…"), shown where it lives: in the pinned tab, else
 *  in Plugins (over its list). */
export function openDestination(id: string, screenOf: (d: Destination) => Screen): void {
  paintTabs();
  const d = dests.find((x) => x.id === id || x.id === id.split('~', 1)[0]);
  if (!d) return;
  if (pin?.id === d.id) { showOn('pin', null); return; }
  showOn('plugins', screenOf(d));
}
