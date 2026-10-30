// The item pane's parts for papers from a plugin's list (views "source:…"): the header and
// fields of a paper outside the library (read-only: it is not stored anywhere yet), and "Why it is
// here", the reasons the plugin gives, for library items in such a list too.
import { api } from '../../api.js';
import { icon, typeIcon } from '../../icons.js';
import { fieldText, itemTypeLabel, state } from '../../store.js';
import type { Item, Reason } from '../../types.js';
import { escapeHtml, shortAuthors } from '../../util.js';

const REASON_ICON: Record<string, string> = { cites: 'link', cites_own: 'link', cited_by: 'link', authors: 'library', similar: 'search' };

/** The header of a paper outside the library, with "Add to My Library". */
export function outsideHeader(it: Item): string {
  const type = it.item_type || 'journalArticle';
  const src = state.sources.find((s) => s.id === it._source);
  return `<div class="ip-header">
      <div class="ip-type">${typeIcon(type, 'icon-sm')} <span>${escapeHtml(itemTypeLabel(type))}</span>
        <span class="badge" title="${escapeHtml(src ? `From ${src.title} (${src.plugin_name})` : '')}">Not in your library</span></div>
      <h2 class="ip-title">${escapeHtml(it.title || 'Untitled')}</h2>
      <div class="ip-sub">${escapeHtml([shortAuthors(it.authors), it.year, it.journal].filter(Boolean).join(' · '))}</div>
      <div class="ip-actions">
        <button class="btn btn-sm btn-primary" data-out="add">${icon('plus', 'icon-sm')} Add to My Library</button>
        <button class="btn btn-sm" data-out="add-folder">${icon('folderPlus', 'icon-sm')} Add to Folder…</button>
        ${it.url ? `<button class="btn btn-sm" data-out="online">${icon('globe', 'icon-sm')} View Online</button>` : ''}
      </div>
    </div>`;
}

/** Its fields, read-only. */
export function outsideInfo(it: Item): string {
  const row = (label: string, value: string) => (value ? `<div class="field-row"><label>${escapeHtml(label)}</label><div class="fv readonly">${value}</div></div>` : '');
  const link = (href: string, text: string) => `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(text)}</a>`;
  return [
    row('Title', escapeHtml(it.title || '')),
    row('Authors', escapeHtml((it.authors || '').split(';').map((a) => a.trim()).filter(Boolean).join('; '))),
    row('Year', escapeHtml(String(it.year || ''))),
    row('Date', escapeHtml(String(it.date || ''))),
    row('Publication', escapeHtml(it.journal || '')),
    row('arXiv', it.arxiv ? link(`https://arxiv.org/abs/${it.arxiv}`, it.arxiv) : ''),
    row('Categories', escapeHtml(it.categories || '')),
    row('DOI', it.doi ? link(`https://doi.org/${it.doi}`, it.doi) : ''),
  ].join('');
}

/** What plugins' fields say about it (their values come with the list). */
export function outsideFields(it: Item): string {
  return state.fieldDefs.filter((f) => it._f?.[f.id] !== undefined && it._f?.[f.id] !== null).map((f) =>
    `<div class="field-row"><label title="${escapeHtml(`${f.label} (${f.plugin_name})`)}">${escapeHtml(f.label)}</label><div class="fv readonly">${escapeHtml(fieldText(f, it._f![f.id]))}</div></div>`).join('');
}

/** "Why it is here": the plugin's reasons; the library papers they name are filled in by fillReasons. */
export function reasonsBody(reasons: Reason[]): string {
  return `<div class="ip-reasons">${reasons.map((r, i) => `
    <div class="ip-reason">${icon(REASON_ICON[r.kind] || 'info', 'icon-sm')}<div><div>${escapeHtml(r.text)}</div>
      ${r.items.length ? `<div class="ip-reason-items" data-reason="${i}"></div>` : ''}</div></div>`).join('')}</div>`;
}

/** The library papers each reason names, as links that show them in the library. */
export async function fillReasons(el: HTMLElement, reasons: Reason[], alive: () => boolean): Promise<void> {
  const ids = [...new Set(reasons.flatMap((r) => r.items.slice(0, 5)))];
  if (!ids.length) return;
  let items: Item[];
  try { items = (await api.itemsByIds(ids)).items; } catch { return; }
  if (!alive()) return;
  const byId = new Map(items.map((i) => [i.id, i]));
  reasons.forEach((r, n) => {
    const box = el.querySelector<HTMLElement>(`[data-reason="${n}"]`);
    if (!box) return;
    const shown = r.items.slice(0, 5).map((id) => byId.get(id)).filter((i): i is Item => !!i);
    box.innerHTML = shown.map((i) => `<button class="ip-ref in-lib" data-show-item="${escapeHtml(i.id)}" title="Show in the library">${icon('book', 'icon-sm')}
      <span class="r-main"><span class="r-title">${escapeHtml(i.title || 'Untitled')}</span><span class="r-sub">${escapeHtml([shortAuthors(i.authors), i.year].filter(Boolean).join(' · '))}</span></span></button>`).join('')
      + (r.items.length > shown.length ? `<div class="muted">and ${r.items.length - shown.length} more</div>` : '');
  });
}
