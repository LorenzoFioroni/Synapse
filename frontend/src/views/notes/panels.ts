// The panels around a note: the notes list on the left and the note's details on the
// right. While Notes is open, the toolbar's two panel buttons (the item list's column and item
// pane buttons in the Library) hide and show them; Cmd/Ctrl+\ does the same for the list.
// A hidden list slides in over the note when the pointer reaches the left edge of the window
// (or with a tap or click there) and slides out again when the pointer leaves it or a note is
// chosen, so other notes stay one gesture away.
import { icon } from '../../icons.js';
import { on } from '../../store.js';
import { modLabel, storage, store } from '../../util.js';

const LIST = 'zc.notes.listHidden';
const DETAILS = 'zc.notes.details';      // 'shown' | 'hidden' | absent: shown on wide windows
const wide = matchMedia('(min-width: 1101px)');

let root: HTMLElement, pane: HTMLElement, edge: HTMLElement;
let peekTimer: ReturnType<typeof setTimeout> | undefined, closeTimer: ReturnType<typeof setTimeout> | undefined;
const saved = new Map<string, { html: string; title: string }>();                 // the toolbar buttons as the Library has them

const isNotes = () => document.body.dataset.workspace === 'notes';
export const listHidden = () => !!storage(LIST, false);
export const peeking = () => document.body.classList.contains('notes-peek');

function detailsShown() {
  const pref = storage<string | null>(DETAILS, null);
  return pref === 'shown' || (pref !== 'hidden' && wide.matches);
}

/** Keep a peeking list open while you type in it or use one of its menus or dialogs. */
function keepOpen() {
  const a = document.activeElement;
  return (!!a && pane.contains(a) && a.matches('input, textarea, [contenteditable="true"]'))
    || !!document.querySelector('.menu, .dialog-backdrop');
}

/** The list over the note sits between the toolbar and the status bar. */
function place() {
  const r = document.getElementById('app')?.getBoundingClientRect();
  if (!r) return;
  root.style.setProperty('--app-top', `${Math.round(r.top)}px`);
  root.style.setProperty('--app-bottom', `${Math.max(0, Math.round(innerHeight - r.bottom))}px`);
}

export function peek(open: boolean): void {
  clearTimeout(peekTimer);
  clearTimeout(closeTimer);
  const on_ = !!open && listHidden() && isNotes();
  if (on_) place();
  document.body.classList.toggle('notes-peek', on_);
}

function scheduleClose() {
  clearTimeout(closeTimer);
  closeTimer = setTimeout(() => {
    if (!peeking() || pane.matches(':hover')) return;
    if (keepOpen()) return;   // closes on blur, Escape or a click elsewhere
    peek(false);
  }, 350);
}

function apply() {
  document.body.classList.toggle('notes-list-hidden', listHidden());
  const pref = storage<string | null>(DETAILS, null);
  root.classList.toggle('details-hidden', pref === 'hidden');
  root.classList.toggle('details-shown', pref === 'shown');
  if (!listHidden()) peek(false);
  paintToolbar();
}

export function toggleList() {
  store(LIST, !listHidden());
  apply();
}

export function toggleDetails() {
  store(DETAILS, detailsShown() ? 'hidden' : 'shown');
  apply();
}

/** The toolbar's two panel buttons act on the notes while Notes is open. */
export function panelButton(a: string): void {
  if (a === 'columns') toggleList();
  else if (a === 'pane') toggleDetails();
}

function paintToolbar() {
  for (const [a, ic, title] of <[string, string, () => string][]>[
    ['columns', 'sidebarLeft', () => `${listHidden() ? 'Show' : 'Hide'} the notes list (${modLabel}\\)`],
    ['pane', 'sidebar', () => `${detailsShown() ? 'Hide' : 'Show'} the note details`],
  ]) {
    const b = document.querySelector<HTMLElement>(`#toolbar [data-a="${a}"]`);
    if (!b) continue;
    if (!saved.has(a)) saved.set(a, { html: b.innerHTML, title: b.title });
    if (isNotes()) {
      b.innerHTML = icon(ic);
      b.title = title();
      b.setAttribute('aria-pressed', String(a === 'columns' ? !listHidden() : detailsShown()));
    } else {
      b.innerHTML = saved.get(a)!.html;
      b.title = saved.get(a)!.title;
      b.removeAttribute('aria-pressed');
    }
  }
}

export function initPanels(notesRoot: HTMLElement): void {
  root = notesRoot;
  pane = root.querySelector<HTMLElement>('.notes-tree-pane')!;
  edge = document.createElement('div');
  edge.className = 'notes-edge';
  edge.title = 'Notes list';
  root.appendChild(edge);

  edge.addEventListener('pointerenter', (e) => {
    if (e.pointerType !== 'mouse') return;
    clearTimeout(peekTimer);
    peekTimer = setTimeout(() => peek(true), 120);   // passing by on the way to the window's edge does not open it
  });
  edge.addEventListener('pointerleave', () => clearTimeout(peekTimer));
  edge.addEventListener('click', () => peek(!peeking()));   // a tap, a pen, or a click
  pane.addEventListener('pointerenter', () => clearTimeout(closeTimer));
  pane.addEventListener('pointerleave', (e) => { if (peeking() && e.pointerType === 'mouse') scheduleClose(); });
  pane.addEventListener('focusout', () => setTimeout(() => { if (peeking() && !pane.matches(':hover') && !keepOpen()) peek(false); }, 0));
  // a note chosen from the list: the list makes room for it again
  pane.addEventListener('click', (e) => { if (peeking() && (e.target as Element).closest('.nt-row.note, .nt-hit')) setTimeout(() => peek(false), 150); });
  document.addEventListener('pointerdown', (e) => {
    if (peeking() && !(e.target as Element).closest('.notes-tree-pane, .notes-edge, .menu, .dialog-backdrop')) peek(false);
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && peeking() && !document.querySelector('.menu, .dialog-backdrop')) peek(false);
  });
  wide.addEventListener?.('change', paintToolbar);
  window.addEventListener('resize', () => { if (peeking()) place(); });
  on('workspace', () => { if (!isNotes()) peek(false); paintToolbar(); });
  apply();
}
