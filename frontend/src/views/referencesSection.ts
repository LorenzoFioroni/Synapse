// Item pane: "References" — what the item cites (fetched online, the base of the citation
// graph) and which library items cite it. Works that are in the library link to them; the
// others can be added by DOI / arXiv ID with one click.
import * as A from '../actions.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { byId, emit, state } from '../store.js';
import { progressToast, toast, toastError } from '../ui/toast.js';
import type { ApiError } from '../api.js';
import type { Item, ItemReferences } from '../types.js';
import { escapeHtml, formatDate, plural, shortAuthors } from '../util.js';

const SOURCES: Record<string, string> = { semanticscholar: 'Semantic Scholar', openalex: 'OpenAlex', crossref: 'Crossref', none: 'no service' };
const SHOW = 8;
const expanded = new Set<string>();   // items whose full list is open
let pollTimer: ReturnType<typeof setTimeout> | undefined;

/** Fill the section of item ``it``; ``current()`` says whether the pane still shows it. */
export async function fillReferences(el: HTMLElement | null, it: Item, current: () => boolean): Promise<void> {
  if (!el) return;
  clearTimeout(pollTimer);
  let v: ItemReferences;
  try { v = await api.itemReferences(it.id); } catch (e) { el.innerHTML = `<span class="muted">${escapeHtml((e as Error).message)}</span>`; return; }
  if (!current() || !el.isConnected) return;
  paint(el, it, v);
  if (v.queued) pollTimer = setTimeout(() => fillReferences(el, it, current), 2000);   // fetching in the background
  if (!el.dataset.bound) {
    el.dataset.bound = '1';
    el.addEventListener('click', (e) => onClick(e, el, it, current));
  }
}

function paint(el: HTMLElement, it: Item, v: ItemReferences): void {
  const f = v.fetch;
  const refs = v.references || [];
  const inLib = refs.filter((r) => r.library_id).length;
  let status: string;
  if (v.queued) status = '<span class="spinner"></span> Fetching the reference list…';
  else if (!f) status = v.can_fetch ? 'Not fetched yet.' : 'Needs a DOI or arXiv ID to look up its references.';
  else if (f.error && !f.count) status = `<span class="warn">${escapeHtml(f.error)}</span>`;
  else if (!f.count) status = `No reference list found (${escapeHtml(formatDate(f.fetched_at))}).`;
  else {
    status = `${plural(f.count, 'reference')}${inLib ? ` · <b>${inLib}</b> in your library` : ''}
      <span class="muted">· ${escapeHtml(SOURCES[f.source] || f.source)}, ${escapeHtml(formatDate(f.fetched_at))}</span>`;
  }
  const btn = v.can_fetch && !v.queued && state.view !== 'trash'
    ? `<button class="btn btn-sm" data-ref="fetch">${icon('refresh', 'icon-sm')} ${f ? 'Refresh' : 'Fetch references'}</button>` : '';
  const citedBy = (v.cited_by || []).map((c) => `
    <button class="ip-ref in-lib" data-show-item="${escapeHtml(c.id)}" title="Show in the library">${icon('book', 'icon-sm')}
      <span class="r-main"><span class="r-title">${escapeHtml(c.title || 'Untitled')}</span>
      <span class="r-sub">${escapeHtml([shortAuthors(c.authors), c.year].filter(Boolean).join(' · '))}</span></span></button>`).join('');
  const all = expanded.has(it.id);
  const shown = all ? refs : refs.slice(0, SHOW);
  const rows = shown.map((r) => {
    const sub = [shortAuthors(r.authors), r.year, r.venue].filter(Boolean).join(' · ');
    const title = r.title || r.raw || r.doi || (r.arxiv_id && `arXiv:${r.arxiv_id}`) || 'Unknown work';
    if (r.library_id) {
      return `<button class="ip-ref in-lib" data-show-item="${escapeHtml(r.library_id)}" title="In your library — click to show it">${icon('book', 'icon-sm')}
        <span class="r-main"><span class="r-title">${escapeHtml(title)}</span><span class="r-sub">${escapeHtml(sub)}</span></span></button>`;
    }
    const ident = r.doi || (r.arxiv_id ? `arXiv:${r.arxiv_id}` : '');
    const href = r.doi ? `https://doi.org/${r.doi}` : (r.arxiv_id ? `https://arxiv.org/abs/${r.arxiv_id}` : '');
    return `<div class="ip-ref">${icon('doc', 'icon-sm')}
      <span class="r-main">${href ? `<a class="r-title" href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(title)}</a>` : `<span class="r-title">${escapeHtml(title)}</span>`}
        <span class="r-sub">${escapeHtml(sub)}</span></span>
      ${ident && state.view !== 'trash' ? `<button class="btn btn-sm btn-ghost r-add" data-add-ident="${escapeHtml(ident)}" title="Add to your library (${escapeHtml(ident)})">${icon('plus', 'icon-sm')}</button>` : ''}</div>`;
  }).join('');
  el.innerHTML = `
    <div class="ip-refs-status"><span>${status}</span>${btn}</div>
    ${citedBy ? `<div class="ip-cited-label">Cited by in your library <span class="count">${v.cited_by.length}</span></div>${citedBy}` : ''}
    ${rows ? `<div class="ip-cited-label">Cites</div>${rows}` : ''}
    ${refs.length > SHOW ? `<button class="btn btn-sm btn-ghost ip-refs-more" data-ref="more">${all ? 'Show fewer' : `Show all ${refs.length}`}</button>` : ''}`;
  const head = el.closest('[data-section]')?.querySelector('.section-head');
  head?.querySelector('.count')?.remove();
  if (refs.length) head?.insertAdjacentHTML('beforeend', ` <span class="count">${refs.length}</span>`);
}

async function onClick(e: MouseEvent, el: HTMLElement, it: Item, current: () => boolean): Promise<void> {
  const target = e.target as Element;
  const show = target.closest<HTMLElement>('[data-show-item]');
  if (show) { emit('show-item', show.dataset.showItem!); return; }
  const act = target.closest<HTMLElement>('[data-ref]')?.dataset.ref;
  if (act === 'more') { if (expanded.has(it.id)) expanded.delete(it.id); else expanded.add(it.id); fillReferences(el, it, current); return; }
  if (act === 'fetch') {
    const b = target.closest('button')!;
    b.disabled = true;
    b.innerHTML = '<span class="spinner"></span> Fetching…';
    try {
      const v = await api.fetchReferences(it.id);
      if (current() && el.isConnected) paint(el, it, v);
      toast(v.fetch?.count ? `Found ${plural(v.fetch.count, 'reference')}.` : 'No reference list found for this item.');
    } catch (err) { toastError(err); if (current() && el.isConnected) fillReferences(el, it, current); }
    return;
  }
  const add = target.closest<HTMLButtonElement>('[data-add-ident]');
  if (add) {
    add.disabled = true;
    try {
      await api.fromIdentifier(add.dataset.addIdent!, false, null);
      toast('Added to your library.', { type: 'success' });
    } catch (err) {
      if ((err as ApiError).status === 409) toast('Already in your library.');
      else { toastError(err); add.disabled = false; return; }
    }
    await A.loadItems();
    if (current() && el.isConnected) fillReferences(el, it, current);
  }
}

/**
 * Fetch the reference lists of several items (item menu, multi-selection pane), one by one
 * with a progress toast that can be stopped. Items without a DOI / arXiv ID are skipped.
 */
export async function fetchReferencesBulk(ids: string[]): Promise<void> {
  if (!ids.length) return;
  let cancelled = false;
  const pt = progressToast('Fetching references…', { onCancel: () => { cancelled = true; } });
  const found: string[] = [], none: string[] = [], noId: string[] = [], failed: string[] = [];
  let linked = 0;
  for (let n = 0; n < ids.length && !cancelled; n++) {
    const it = byId(ids[n]);
    pt.set(`Fetching references ${n + 1}/${ids.length}: ${(it?.title || 'Untitled').slice(0, 60)}`, n, ids.length);
    try {
      const v = await api.fetchReferences(ids[n]);
      (v.fetch?.count ? found : none).push(ids[n]);
      linked += (v.references || []).filter((r) => r.library_id).length;
    } catch (err) {
      const e = err as ApiError;
      if (e.status === 404) noId.push(ids[n]);
      else {
        failed.push(ids[n]);
        if (e.status === 0) break;    // server gone: stop instead of failing every item
      }
    }
    if (n < ids.length - 1) await new Promise((r) => setTimeout(r, 400));   // be gentle with the free APIs
  }
  pt.close();
  const missing = [...none, ...noId, ...failed];
  const parts = [`References for ${plural(found.length, 'item')}`];
  if (linked) parts.push(`${linked} cited works in your library`);
  if (none.length) parts.push(`${none.length} with no list found`);
  if (noId.length) parts.push(`${noId.length} without DOI/arXiv ID`);
  if (failed.length) parts.push(`${failed.length} failed`);
  if (cancelled) parts.push('stopped early');
  toast(parts.join(' · '), {
    type: found.length ? 'success' : 'error',
    timeout: 8000,
    action: missing.length && ids.length > 1 ? { label: 'Select the others', run: () => A.select(missing, { anchor: missing[0] }) } : null,
  });
  emit('items');   // repaint the item pane with the new lists
}
