// Citation card: shown when hovering a citation (editor chips, read view, side panel).
// Lists the cited item(s) with "Open in library", "Open PDF (p. N)" and the paper note.
import { api, openAttachment } from '../../api.js';
import { icon } from '../../icons.js';
import { emit } from '../../store.js';
import { toastError } from '../../ui/toast.js';
import { escapeHtml } from '../../util.js';
import { shortAuthors } from './library.js';

let card = null;
let anchor = null;
let showTimer = null;
let hideTimer = null;
let deps = null;      // { itemInfo(key), openNote(path), refreshLibrary() }

const HOVER = '.cm-lp-chip.cite, .cm-lp-citesrc[data-keys], .prose .cite, [data-cite-keys]';

function keysOf(el) {
  if (el.dataset.keys) return el.dataset.keys.split(';').filter(Boolean);
  if (el.dataset.citeKeys) return el.dataset.citeKeys.split(';').filter(Boolean);
  return el.dataset.key ? [el.dataset.key] : [];
}
function pagesOf(el) {
  if (el.dataset.pages) return el.dataset.pages.split(';').map((p) => (p ? Number(p) : null));
  return [el.dataset.page ? Number(el.dataset.page) : null];
}

function ensureCard() {
  if (card) return card;
  card = document.createElement('div');
  card.className = 'cite-card';
  card.hidden = true;
  card.addEventListener('mouseenter', () => clearTimeout(hideTimer));
  card.addEventListener('mouseleave', () => scheduleHide());
  card.addEventListener('mousedown', (e) => e.preventDefault());   // keep the editor's selection
  card.addEventListener('click', onCardClick);
  document.body.appendChild(card);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
  document.addEventListener('mousedown', (e) => { if (card && !card.hidden && !card.contains(e.target)) hide(); }, true);
  return card;
}

function render(keys, pages) {
  return keys.map((key, i) => {
    const it = deps.itemInfo(key);
    const page = pages[i] ?? null;
    if (!it) {
      return `<div class="cc-item missing"><div class="cc-key">@${escapeHtml(key)}</div>
        <div class="cc-title">Not in your library</div>
        <div class="cc-sub">Add it (for example by DOI) and give it this citation key.</div></div>`;
    }
    return `<div class="cc-item" data-key="${escapeHtml(key)}">
      <div class="cc-key">@${escapeHtml(key)}${it.trashed ? ' <span class="badge warn">In Trash</span>' : ''}</div>
      <div class="cc-title">${escapeHtml(it.title || 'Untitled')}</div>
      <div class="cc-sub">${escapeHtml([shortAuthors(it.authors), it.year, it.journal].filter(Boolean).join(' · '))}</div>
      <div class="cc-actions">
        <button class="btn btn-sm" data-cc="item">${icon('library', 'icon-sm')} Open in library</button>
        ${it.pdf ? `<button class="btn btn-sm" data-cc="pdf" data-page="${page ?? ''}">${icon('pdf', 'icon-sm')} PDF${page ? ` p. ${page}` : ''}</button>` : ''}
        ${it.note ? `<button class="btn btn-sm" data-cc="note">${icon('edit', 'icon-sm')} Paper note</button>`
    : (it.trashed ? '' : `<button class="btn btn-sm btn-ghost" data-cc="new-note">${icon('plus', 'icon-sm')} Paper note</button>`)}
      </div></div>`;
  }).join('');
}

function place(el) {
  const r = el.getBoundingClientRect();
  card.style.left = '0px';
  card.style.top = '0px';
  card.hidden = false;
  const w = card.offsetWidth, h = card.offsetHeight;
  const left = Math.max(8, Math.min(r.left, innerWidth - w - 8));
  const below = r.bottom + 6;
  const top = below + h > innerHeight - 8 ? Math.max(8, r.top - h - 6) : below;
  card.style.left = `${left}px`;
  card.style.top = `${top}px`;
}

/** Show the card for a citation element now. */
export function showCard(el) {
  ensureCard();
  clearTimeout(hideTimer);
  clearTimeout(showTimer);
  const keys = keysOf(el);
  if (!keys.length) return;
  anchor = el;
  card.innerHTML = render(keys, pagesOf(el));
  card.dataset.pages = pagesOf(el).join(';');
  place(el);
}

export function hide() {
  clearTimeout(showTimer);
  if (card) card.hidden = true;
  anchor = null;
}
function scheduleHide() {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(hide, 250);
}

async function onCardClick(e) {
  const b = e.target.closest('[data-cc]');
  if (!b) return;
  const key = b.closest('[data-key]')?.dataset.key;
  const it = key && deps.itemInfo(key);
  if (!it) return;
  const act = b.dataset.cc;
  hide();
  if (act === 'item') emit('show-item', it.id);
  if (act === 'pdf') openAttachment({ id: it.id, title: it.title }, it.pdf, b.dataset.page ? Number(b.dataset.page) : undefined);
  if (act === 'note') deps.openNote(it.note);
  if (act === 'new-note') {
    try {
      const res = await api.createNote({ item_id: it.id });
      await deps.refreshLibrary();
      deps.openNote(res.path);
    } catch (err) {
      if (err.data?.path) deps.openNote(err.data.path); else toastError(err);
    }
  }
}

/**
 * Hover cards inside `container`. Plain click on a read-mode citation opens the PDF at the
 * cited page (or the item); in the editor, Cmd/Ctrl+click does the same and a plain click edits.
 */
export function attachCiteCards(container, d) {
  deps = d;
  container.addEventListener('mouseover', (e) => {
    const el = e.target.closest?.(HOVER);
    if (!el || el === anchor) { if (el) clearTimeout(hideTimer); return; }
    clearTimeout(showTimer);
    showTimer = setTimeout(() => { if (el.isConnected) showCard(el); }, 350);
  });
  container.addEventListener('mouseout', (e) => {
    const el = e.target.closest?.(HOVER);
    if (!el) return;
    clearTimeout(showTimer);
    if (!e.relatedTarget || !card?.contains(e.relatedTarget)) scheduleHide();
  });
}

/** Default action for a citation: the PDF at the cited page if there is one, else the item. */
export function openCitation(key, page) {
  const it = deps?.itemInfo(key);
  if (!it) return false;
  if (page && it.pdf) openAttachment({ id: it.id, title: it.title }, it.pdf, page);
  else location.hash = `item=${it.id}`;
  return true;
}
