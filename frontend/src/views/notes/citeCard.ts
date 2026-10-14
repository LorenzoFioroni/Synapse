// Citation card: shown when hovering a citation (editor chips, read view, side panel).
// Lists the cited item(s) with "Open in library", "Open PDF (p. N)" and the paper note. An item
// with several PDFs gets one PDF button each; a citation with att:<id> names the one it means.
import { type ApiError, api, openAttachment, pdfTabFor, pointPdfTab } from '../../api.js';
import { icon } from '../../icons.js';
import { emit } from '../../store.js';
import { toastError } from '../../ui/toast.js';
import { escapeHtml } from '../../util.js';
import type { CiteEntry, CitePdf } from '../../types.js';
import { type CiteTarget, citeTargetInDom, viewerTarget } from './citeTarget.js';
import { shortAuthors } from './library.js';
import { imageSrc } from './render.js';

/** What the cards need from the notes view. */
export interface CiteCardDeps {
  itemInfo(key: string): CiteEntry | null | undefined;
  openNote(path: string): void;
  refreshLibrary(): Promise<unknown>;
  /** Where a citation element in the editor points (its quote or snapshot), if not from the DOM. */
  targetOf?(el: HTMLElement): CiteTarget | null | undefined;
}

let card: HTMLElement | null = null;
let anchor: HTMLElement | null = null;
let showTimer: ReturnType<typeof setTimeout> | undefined;
let hideTimer: ReturnType<typeof setTimeout> | undefined;
let deps: CiteCardDeps | null = null;

const HOVER = '.cm-lp-chip.cite, .cm-lp-citesrc[data-keys], .prose .cite, [data-cite-keys]';

function keysOf(el: HTMLElement): string[] {
  if (el.dataset.keys) return el.dataset.keys.split(';').filter(Boolean);
  if (el.dataset.citeKeys) return el.dataset.citeKeys.split(';').filter(Boolean);
  return el.dataset.key ? [el.dataset.key] : [];
}
function pagesOf(el: HTMLElement): (number | null)[] {
  if (el.dataset.pages) return el.dataset.pages.split(';').map((p) => (p ? Number(p) : null));
  return [el.dataset.page ? Number(el.dataset.page) : null];
}
function attsOf(el: HTMLElement): (number | null)[] {
  if (el.dataset.atts) return el.dataset.atts.split(';').map((a) => (a ? Number(a) : null));
  return [el.dataset.att ? Number(el.dataset.att) : null];
}

/** The PDFs of a library item (the first one is the default a citation without att: means). */
export function pdfsOf(it: Pick<CiteEntry, 'pdf' | 'pdfs'> | null | undefined): CitePdf[] {
  if (it?.pdfs?.length) return it.pdfs;
  return it?.pdf ? [it.pdf] : [];
}
const shortName = (n: string) => (n || 'PDF').length > 26 ? `${n.slice(0, 25)}…` : (n || 'PDF');

function ensureCard(): HTMLElement {
  if (card) return card;
  card = document.createElement('div');
  card.className = 'cite-card';
  card.hidden = true;
  card.addEventListener('mouseenter', () => clearTimeout(hideTimer));
  card.addEventListener('mouseleave', () => scheduleHide());
  card.addEventListener('pointerdown', (e) => e.preventDefault());   // keep the editor's selection
  card.addEventListener('click', onCardClick);
  card.addEventListener('auxclick', (e) => { if (e.button === 1) onCardClick(e); });   // middle click: new tab
  document.body.appendChild(card);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
  document.addEventListener('pointerdown', (e) => { if (card && !card.hidden && !card.contains(e.target as Node)) hide(); }, true);
  return card;
}

function render(keys: string[], pages: (number | null)[], atts: (number | null)[]): string {
  return keys.map((key, i) => {
    const it = deps!.itemInfo(key);
    const page = pages[i] ?? null;
    const att = atts[i] ?? null;
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
        ${pdfButtons(it, page, att)}
        ${it.note ? `<button class="btn btn-sm" data-cc="note">${icon('edit', 'icon-sm')} Paper note</button>`
    : (it.trashed ? '' : `<button class="btn btn-sm btn-ghost" data-cc="new-note">${icon('plus', 'icon-sm')} Paper note</button>`)}
      </div></div>`;
  }).join('');
}

/** One button per PDF the citation can mean: the named one, else every PDF of the item. */
function pdfButtons(it: CiteEntry, page: number | null, att: number | null): string {
  const all = pdfsOf(it);
  const named = att ? all.filter((p) => p.id === att) : [];
  const list = named.length ? named : all;
  const label = (p: CitePdf) => (list.length > 1 ? shortName(p.name) : 'PDF') + (page ? ` p. ${page}` : '');
  return list.map((p) => `<button class="btn btn-sm" data-cc="pdf" data-att="${p.id}" data-page="${page ?? ''}" title="${escapeHtml(p.name || 'PDF')}">${icon('pdf', 'icon-sm')} ${escapeHtml(label(p))}</button>`).join('');
}

function place(el: HTMLElement): void {
  const card = ensureCard();
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
export function showCard(el: HTMLElement): void {
  const card = ensureCard();
  clearTimeout(hideTimer);
  clearTimeout(showTimer);
  const keys = keysOf(el);
  if (!keys.length) return;
  anchor = el;
  card.innerHTML = render(keys, pagesOf(el), attsOf(el));
  card.dataset.pages = pagesOf(el).join(';');
  place(el);
}

export function hide(): void {
  clearTimeout(showTimer);
  if (card) card.hidden = true;
  anchor = null;
}
function scheduleHide() {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(hide, 250);
}

async function onCardClick(e: MouseEvent): Promise<void> {
  const b = (e.target as Element).closest<HTMLElement>('[data-cc]');
  if (!b || !deps) return;
  const key = b.closest<HTMLElement>('[data-key]')?.dataset.key;
  const it = key && deps.itemInfo(key);
  if (!key || !it) return;
  const act = b.dataset.cc;
  const from = anchor;
  hide();
  if (act === 'item') emit('show-item', it.id);
  if (act === 'pdf') {
    const page = b.dataset.page ? Number(b.dataset.page) : undefined;
    const att = b.dataset.att ? Number(b.dataset.att) : null;
    openCitation(key, page ?? null, from && (deps.targetOf?.(from) ?? citeTargetInDom(from)), { pdfOnly: true, att, newTab: e.metaKey || e.ctrlKey || e.button === 1 });
  }
  if (act === 'note') deps.openNote(it.note!);
  if (act === 'new-note') {
    try {
      const res = await api.createNote({ item_id: it.id });
      await deps.refreshLibrary();
      deps.openNote(res.path);
    } catch (err) {
      const existing = ((err as ApiError).data as { path?: string } | undefined)?.path;   // the item has a paper note already
      if (existing) deps.openNote(existing); else toastError(err);
    }
  }
}

/**
 * Hover cards inside `container`. Plain click on a read-mode citation opens the PDF at the
 * cited page (or the item); in the editor, Cmd/Ctrl+click does the same and a plain click edits.
 * The PDF's tab is reused if it is open; Cmd/Ctrl+click in read mode (Cmd/Ctrl+Shift+click in
 * the editor) or a middle click opens a new tab instead.
 */
export function attachCiteCards(container: HTMLElement, d: CiteCardDeps): void {
  deps = d;
  const citeAt = (e: Event) => (e.target as Element).closest?.<HTMLElement>(HOVER);
  container.addEventListener('mouseover', (e) => {
    const el = citeAt(e);
    if (!el || el === anchor) { if (el) clearTimeout(hideTimer); return; }
    clearTimeout(showTimer);
    clearTimeout(hideTimer);   // moving straight to another citation: don't let the old card's hide cancel this one
    showTimer = setTimeout(() => { if (el.isConnected) showCard(el); }, 350);
  });
  // touch screens: a long press opens the card (ui/longpress.js); a tap does what a click does
  container.addEventListener('zc:longpress', (e) => {
    const el = citeAt(e);
    if (!el) return;
    e.preventDefault();
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    showCard(el);
  });
  container.addEventListener('mouseout', (e) => {
    const el = citeAt(e);
    if (!el) return;
    clearTimeout(showTimer);
    if (!e.relatedTarget || !card?.contains(e.relatedTarget as Node)) scheduleHide();
  });
}

/**
 * Default action for a citation: the PDF at the cited page if there is one — at the quoted words
 * or the snapshot's region when `target` says so (citeTarget.js) — else the item.
 */
export function openCitation(key: string, page: number | null, target: CiteTarget | null = null,
  { pdfOnly = false, newTab = false, att = null }: { pdfOnly?: boolean; newTab?: boolean; att?: number | null } = {}): boolean {
  const it = deps?.itemInfo(key);
  if (!it) return false;
  const pdfs = pdfsOf(it);
  const pdf = pdfs.find((p) => p.id === att) || pdfs[0];   // the PDF the citation names, else the first
  if ((page || pdfOnly) && pdf) {
    // The PDF's tab is reused (scrolled to the spot and brought forward) unless `newTab`.
    // A snapshot's region is read from its image first: take the tab right away (popup blockers
    // want it inside the click), then point it.
    const tab = !target || 'quote' in target ? null : pdfTabFor(pdf, { newTab });
    viewerTarget(target, { imageUrl: imageSrc }).then((at) => {
      const p = page || at.page || undefined;
      if (!tab) return openAttachment({ id: it.id, title: it.title }, pdf, p, at, { newTab });
      pointPdfTab(tab, { id: it.id, title: it.title }, pdf, p, at);
    });
  } else location.hash = `item=${it.id}`;
  return true;
}

