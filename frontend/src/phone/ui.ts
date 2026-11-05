// Small pieces the phone screens share: their headers, sheets that rise from the bottom, rows and
// cards of papers, and swiping a row.
import { icon } from '../icons.js';
import type { Item } from '../types.js';
import { escapeHtml as esc, h } from '../util.js';
import { itemMeta, swipeAction } from './model.js';

/** The header of a tab's first screen: a large title, a line under it and buttons on the right. */
export function rootHeader(title: string, { kicker = '', actions = '' } = {}): string {
  return `<header class="ph-head">
    <div class="ph-head-text">${kicker ? `<div class="ph-kicker">${esc(kicker)}</div>` : ''}<h1>${esc(title)}</h1></div>
    <div class="ph-head-actions">${actions}</div></header>`;
}

/** The bar of a screen pushed on top: back (named after the screen below), a title, buttons. */
export function navBar(back: string, title = '', actions = ''): string {
  return `<nav class="ph-nav">
    <button class="ph-back" data-ph="back" aria-label="Back to ${esc(back)}">${icon('chevron', 'flip')}<span>${esc(back)}</span></button>
    <div class="ph-nav-title">${esc(title)}</div>
    <div class="ph-nav-actions">${actions}</div></nav>`;
}

export const roundButton = (name: string, label: string, attrs = ''): string =>
  `<button class="ph-round" aria-label="${esc(label)}" title="${esc(label)}" ${attrs}>${icon(name)}</button>`;

export const navButton = (name: string, label: string, attrs = ''): string =>
  `<button class="ph-nav-btn" aria-label="${esc(label)}" title="${esc(label)}" ${attrs}>${icon(name)}</button>`;

export const chevron = (): string => `<span class="ph-chev" aria-hidden="true">${icon('chevron')}</span>`;

export interface Sheet { el: HTMLElement; body: HTMLElement; close(): void }

/** A sheet over the page, rising from the bottom; tapping beside it or swiping it down closes it. */
export function openSheet(title: string, { onClose }: { onClose?: () => void } = {}): Sheet {
  const back = h('<div class="ph-sheet-backdrop"></div>');
  const el = h(`<section class="ph-sheet" role="dialog" aria-label="${esc(title)}">
    <div class="sheet-grabber" aria-hidden="true"></div>
    <header class="ph-sheet-head"><h2>${esc(title)}</h2><button class="ph-sheet-close" aria-label="Close">${icon('close')}</button></header>
    <div class="ph-sheet-body"></div></section>`);
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    el.classList.add('closing'); back.classList.add('closing');
    setTimeout(() => { el.remove(); back.remove(); }, 180);
    document.removeEventListener('keydown', onKey, true);
    onClose?.();
  };
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); close(); } };
  back.addEventListener('click', close);
  el.querySelector('.ph-sheet-close')!.addEventListener('click', close);
  // drag the grabber (or the head) down to close
  const head = el.querySelector<HTMLElement>('.ph-sheet-head')!;
  let startY = -1;
  for (const grip of [head, el.querySelector<HTMLElement>('.sheet-grabber')!]) {
    grip.addEventListener('touchstart', (e) => { startY = e.touches[0].clientY; }, { passive: true });
    grip.addEventListener('touchmove', (e) => {
      if (startY < 0) return;
      const dy = Math.max(0, e.touches[0].clientY - startY);
      el.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    grip.addEventListener('touchend', (e) => {
      const dy = (e.changedTouches[0]?.clientY ?? startY) - startY;
      startY = -1;
      if (dy > 90) close(); else el.style.transform = '';
    });
  }
  document.addEventListener('keydown', onKey, true);
  document.body.append(back, el);
  return { el, body: el.querySelector<HTMLElement>('.ph-sheet-body')!, close };
}

const badgeIcon = (name?: string) => (name === 'star' || name === 'megaphone' || name === 'link' ? icon(name) : '');

/** A plugin's badges of a paper (Following, Cites you …). */
export function badgesHtml(it: Item): string {
  const list = it.highlights?.length ? it.highlights : it.highlight ? [it.highlight] : [];
  return list.map((b) => `<span class="ph-badge tone-${esc(b.tone || 'gold')}" title="${esc(b.text)}">${badgeIcon(b.icon)}${esc(b.label)}</span>`).join('');
}

/** The reason a plugin gives first, and its score line ("Match 82 · Read") apart. */
export function reasonsOf(it: Item): { score: string; why: string } {
  const rs = it.reasons || [];
  const score = rs.find((r) => r.kind === 'match')?.text.replace(/\s*\(.*\)$/, '') || '';
  const why = rs.find((r) => r.kind !== 'match' && r.kind !== 'following')?.text || '';
  return { score, why };
}

/** A paper as a card: badges and score, title, authors, why it is listed, and buttons. */
export function cardHtml(it: Item, { dismiss = false } = {}): string {
  const { score, why } = reasonsOf(it);
  const badges = badgesHtml(it);
  return `<article class="ph-card" data-id="${esc(it.id)}">
    ${badges || score ? `<div class="ph-card-top"><div class="ph-badges">${badges}</div>${score ? `<span class="ph-score">${esc(score)}</span>` : ''}</div>` : ''}
    <h3 class="ph-card-title">${esc(it.title || 'Untitled')}</h3>
    <p class="ph-card-meta">${esc(itemMeta(it))}</p>
    ${why ? `<p class="ph-card-why">${icon('link')}<span>${esc(why)}</span></p>` : ''}
    <div class="ph-card-actions">
      ${it._outside ? `<button class="ph-pill primary" data-act="add">${icon('plus')}Add</button>` : `<span class="ph-in-library">${icon('check')}In your library</span>`}
      ${it.arxiv || it.attachments?.length ? '<button class="ph-pill" data-act="pdf">PDF</button>' : ''}
      <span class="grow"></span>
      ${dismiss ? '<button class="ph-pill quiet" data-act="dismiss">Not for me</button>' : ''}
    </div></article>`;
}

/** A paper as a row: title (two lines at most), authors · year · where, and its badges. */
export function rowHtml(it: Item, { extra = '', marks = '' } = {}): string {
  return `<div class="ph-row-wrap" data-id="${esc(it.id)}"><button class="ph-row" data-id="${esc(it.id)}">
    <span class="ph-row-main"><span class="ph-row-title">${esc(it.title || 'Untitled')}</span>
    <span class="ph-row-meta">${esc(extra || itemMeta(it))}</span></span>
    ${marks ? `<span class="ph-row-marks">${marks}</span>` : ''}</button></div>`;
}

/**
 * Swiping rows inside ``list`` sideways: right adds, left dismisses (what ``act`` does with the
 * row's id). The row follows the finger and shows what letting go will do.
 */
/** What moves under the finger: a row's button (its wrapper shows what letting go does), or a card. */
const mover = (el: HTMLElement): HTMLElement => (el.classList.contains('ph-row-wrap') ? el.firstElementChild as HTMLElement : el);

export function swipeRows(list: HTMLElement, can: (id: string) => { add: boolean; dismiss: boolean }, act: (id: string, what: 'add' | 'dismiss') => void): void {
  let row: HTMLElement | null = null, x0 = 0, y0 = 0, dx = 0, sideways: boolean | null = null, allowed = { add: false, dismiss: false };
  list.addEventListener('touchstart', (e) => {
    const r = (e.target as Element).closest<HTMLElement>('.ph-row-wrap, .ph-card');
    if (!r || (e.target as Element).closest('button.ph-pill')) return;
    allowed = can(r.dataset.id || '');
    if (!allowed.add && !allowed.dismiss) return;
    row = r; x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; dx = 0; sideways = null;
  }, { passive: true });
  list.addEventListener('touchmove', (e) => {
    if (!row) return;
    const t = e.touches[0];
    const mx = t.clientX - x0, my = t.clientY - y0;
    if (sideways === null && Math.abs(mx) + Math.abs(my) > 10) sideways = Math.abs(mx) > Math.abs(my) * 1.3;
    if (!sideways) return;
    dx = Math.max(allowed.dismiss ? -row.offsetWidth : 0, Math.min(allowed.add ? row.offsetWidth : 0, mx));
    mover(row).style.transform = `translateX(${dx}px)`;
    row.classList.add('swiping');
    const what = swipeAction(dx, row.offsetWidth);
    row.dataset.swipe = dx > 0 ? 'add' : dx < 0 ? 'dismiss' : '';
    row.classList.toggle('armed', !!what);
  }, { passive: true });
  const end = () => {
    if (!row) return;
    const r = row, what = swipeAction(dx, r.offsetWidth);
    row = null;
    r.classList.remove('swiping', 'armed');
    if (what) {
      mover(r).style.transform = `translateX(${what === 'add' ? '' : '-'}110%)`;
      r.classList.add('leaving');
      setTimeout(() => act(r.dataset.id || '', what), 160);
    } else { mover(r).style.transform = ''; delete r.dataset.swipe; }
  };
  list.addEventListener('touchend', end);
  list.addEventListener('touchcancel', end);
}
