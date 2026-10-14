// The item list's rows. The list is virtualised: only the rows in and near the viewport are in the
// DOM, between two spacers that stand for the rest. Each item is a "group" (its row, its
// attachment rows when expanded, its full-text snippet), whose height is estimated and then
// measured.
import { icon, typeIcon } from '../../icons.js';
import { byId, emit, itemFolderPaths, itemTypeLabel, on, sortedItems, state, tagColor } from '../../store.js';
import type { Item } from '../../types.js';
import { escapeHtml, formatDate, plural, shortAuthors } from '../../util.js';
import { visibleCols } from './columns.js';
import { emptyState } from './empty.js';

const OVERSCAN = 600;            // px of rows kept above and below the viewport

let pane: HTMLElement, scroller: HTMLElement, head: HTMLElement, body: HTMLElement;
/** The ids in display order (also in state.order, for actions: the DOM only holds the rows on screen). */
export let order: string[] = [];
let shown: Item[] = [];          // the items in display order
let heights: number[] = [];      // height of each group
let offsets = [0];               // offsets[i] = top of group i; offsets[n] = total height
let range: [number, number] | null = null;   // first and last group in the DOM
let posIndex = new Map<string, number>();     // id → index in `shown`
let painted = new Set<string>();              // ids of the rows drawn as selected

export function initRows(els: { pane: HTMLElement; scroller: HTMLElement; head: HTMLElement; body: HTMLElement }): void {
  ({ pane, scroller, head, body } = els);
  on('selection', () => { if (state.cursor) scrollToRow(state.cursor); });
  on('items', renderBody);
  let ticking = false;
  scroller.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { ticking = false; renderWindow(); });
  }, { passive: true });
  new ResizeObserver(() => renderWindow()).observe(scroller);
  on('settings', () => { if (shown.length && Math.abs(heights[0] - estimate(shown[0])) > 0.5) renderBody(); });   // row density
  on('selection', paintSelection);
  on('item-updated', (id) => updateRow(id as string));
}

/* ------------------------------------------------------------------ one row */
function cellHtml(colId: string, it: Item): string {
  switch (colId) {
    case 'attachments': {
      const n = (it.attachments || []).length;
      return n ? `<span class="pdf-dot" data-act="open" title="Open PDF${n > 1 ? ` (${n} attachments)` : ''}">${icon('pdf', 'icon-sm')}</span>` : '';
    }
    case 'notes': {
      const n = state.noteKeys[it.citation_key || ''];
      if (!n) return '';
      const what = [n.paper && 'Paper note', n.citing && `cited in ${plural(n.citing, 'note')}`].filter(Boolean).join(' · ');
      return `<span class="note-dot ${n.paper ? 'paper' : ''}" data-act="note" title="${escapeHtml(what)} — click to open">${icon('edit', 'icon-sm')}</span>`;
    }
    case 'title': {
      const n = (it.attachments || []).length;
      const open = state.expanded.has(it.id);
      const hit = state.hits[it.id]?.fulltext ? ' <span class="badge accent" title="Matched in the full text">full text</span>' : '';
      return `<span class="twisty ${n ? '' : 'none'} ${open ? 'open' : ''}" data-act="toggle">${icon('chevron', 'icon-sm')}</span>`
        + `${typeIcon(it.item_type || '', 'icon-sm type-icon')}<span class="t">${escapeHtml(it.title || 'Untitled')}</span>${hit}${colourDots(it)}`;
    }
    case 'tags': return escapeHtml((it.tags || []).join(', '));
    case 'folder': return escapeHtml(itemFolderPaths(it).join(', '));
    case 'creator': return escapeHtml(shortAuthors(it.authors));
    case 'item_type': return escapeHtml(itemTypeLabel(it.item_type || ''));
    case 'date_added': case 'date_modified': return escapeHtml(formatDate(it[colId]));
    default: return escapeHtml(String(it[colId] ?? ''));
  }
}

/** Coloured tags show as dots after the title, like Zotero. */
function colourDots(it: Item): string {
  const dots = (it.tags || []).map((t) => [t, tagColor(t)] as const).filter(([, c]) => c);
  if (!dots.length) return '';
  return `<span class="row-dots">${dots.map(([t, c]) => `<span class="tag-dot" style="background:${c}" title="${escapeHtml(t)}"></span>`).join('')}</span>`;
}

function rowHtml(it: Item, index = 0): string {
  const cells = visibleCols().map((c) => {
    const title = c.id === 'creator' ? ` title="${escapeHtml(it.authors || '')}"` : (c.id === 'title' ? ` title="${escapeHtml(it.title || '')}"` : '');
    return `<div class="cell c-${c.id}" data-col="${c.id}"${title}>${cellHtml(c.id, it)}</div>`;
  }).join('');
  let html = `<div class="row ${index % 2 ? 'alt' : ''} ${state.selection.has(it.id) ? 'selected' : ''}" role="row" data-id="${it.id}" data-index="${index}" draggable="${state.view === 'trash' ? 'false' : 'true'}">${cells}</div>`;
  if (state.expanded.has(it.id)) {
    for (const a of it.attachments || []) {
      html += `<div class="row child" data-parent="${it.id}" data-att="${a.id}"><div class="cell">${icon('pdf', 'icon-sm')}<span>${escapeHtml(a.name || 'Attachment')}</span></div></div>`;
    }
  }
  const snip = state.hits[it.id]?.fulltext;
  if (snip && state.query) html += `<div class="row snippet" data-parent="${it.id}"><div class="cell">${snip}</div></div>`;
  return html;
}

/* ------------------------------------------------------------------ the window */
function rowHeight(): number {
  return parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--row-h')) || 28;
}

function estimate(it: Item): number {
  const h = rowHeight();
  let total = h;
  if (state.expanded.has(it.id)) total += (it.attachments || []).length * (h - 2);
  if (state.hits[it.id]?.fulltext && state.query) total += 44;
  return total;
}

function computeOffsets(): void {
  offsets = new Array(heights.length + 1);
  offsets[0] = 0;
  for (let i = 0; i < heights.length; i++) offsets[i + 1] = offsets[i] + heights[i];
}

/** Index of the group at y (px from the top of the list). */
function groupAt(y: number): number {
  let lo = 0, hi = heights.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (offsets[mid] <= y) lo = mid; else hi = mid - 1;
  }
  return Math.max(0, lo);
}

function wantedRange(): [number, number] {
  const top = scroller.scrollTop - body.offsetTop;
  return [groupAt(top - OVERSCAN), groupAt(top + scroller.clientHeight + OVERSCAN)];
}

function renderWindow(force = false): void {
  if (!shown.length) { body.innerHTML = ''; range = null; return; }
  const [s, e] = wantedRange();
  if (!force && range && s >= range[0] && e <= range[1]) return;   // still covered
  const extra = Math.ceil(OVERSCAN / rowHeight());                  // a margin, so small scrolls re-render nothing
  const first = Math.max(0, s - extra), last = Math.min(shown.length - 1, e + extra);
  let html = `<div class="vspace" style="height:${offsets[first]}px"></div>`;
  for (let i = first; i <= last; i++) html += rowHtml(shown[i], i);
  html += `<div class="vspace" style="height:${offsets[shown.length] - offsets[last + 1]}px"></div>`;
  body.innerHTML = html;
  range = [first, last];
  painted = new Set([...state.selection].filter((id) => posIndex.has(id)));
  measure();
}

/** Replace estimated heights of the groups in the DOM by their real ones. */
function measure(): void {
  if (!range) return;
  let changed = false;
  let i = -1, h = 0;
  const flush = () => { if (i >= 0 && Math.abs(heights[i] - h) > 0.5) { heights[i] = h; changed = true; } };
  for (const el of body.children as HTMLCollectionOf<HTMLElement>) {
    if (el.classList.contains('vspace')) continue;
    if (el.dataset.index !== undefined) { flush(); i = Number(el.dataset.index); h = 0; }
    h += el.offsetHeight;
  }
  flush();
  if (!changed) return;
  computeOffsets();
  const top = body.firstElementChild as HTMLElement | null, bottom = body.lastElementChild as HTMLElement | null;
  if (top?.classList.contains('vspace')) top.style.height = `${offsets[range[0]]}px`;
  if (bottom?.classList.contains('vspace')) bottom.style.height = `${offsets[shown.length] - offsets[range[1] + 1]}px`;
}

export function renderBody(): void {
  shown = sortedItems();
  order = shown.map((i) => i.id);
  state.order = order;
  posIndex = new Map(order.map((id, i) => [id, i]));
  heights = shown.map(estimate);
  computeOffsets();
  pane.querySelector('.list-empty')?.remove();
  range = null;
  renderWindow(true);
  if (!shown.length) pane.appendChild(emptyState());
  emit('list-rendered', shown.length);
}

function updateRow(id: string): void {
  const i = posIndex.get(id);
  const it = byId(id);
  if (i === undefined || !it) return;
  shown[i] = it;
  heights[i] = estimate(it);
  computeOffsets();
  const row = body.querySelector(`.row[data-id="${CSS.escape(id)}"]`);
  if (!row) { renderWindow(true); return; }   // off screen: only the spacers change
  // remove old children/snippet, re-insert fresh html
  body.querySelectorAll(`[data-parent="${CSS.escape(id)}"]`).forEach((r) => r.remove());
  row.insertAdjacentHTML('afterend', rowHtml(it, i));
  row.remove();
  renderWindow(true);
}

function paintSelection(): void {
  // only touch rows whose state changed (fast with thousands of rows)
  for (const id of painted) if (!state.selection.has(id)) rowEl(id)?.classList.remove('selected');
  for (const id of state.selection) if (!painted.has(id)) rowEl(id)?.classList.add('selected');
  painted = new Set(state.selection);
}
const rowEl = (id: string) => body.querySelector(`.row[data-id="${CSS.escape(id)}"]`);

function scrollToRow(id: string): void {
  const i = posIndex.get(id);
  if (i === undefined) return;
  const top = body.offsetTop + offsets[i], bottom = top + rowHeight();
  const headH = head.offsetHeight;
  if (top - headH < scroller.scrollTop) scroller.scrollTop = top - headH;
  else if (bottom > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = bottom - scroller.clientHeight;
  renderWindow();   // the row is in the DOM right away (keyboard menus look for it)
}

/** Show or hide an item's attachment rows. */
export function toggleExpand(id: string, force?: boolean): void {
  const open = force ?? !state.expanded.has(id);
  if (open) state.expanded.add(id); else state.expanded.delete(id);
  updateRow(id);
}
