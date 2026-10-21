// Pages that plugins add (extend.ts `pages`): each has an entry in the sidebar and, when chosen,
// takes the place of the library the way the Tasks page does. The page draws itself into its
// element; it is taken down when another page, Notes, Tasks or the library is shown, or when the
// plugin stops.
import { pages } from '../extend.js';
import { icon } from '../icons.js';
import { emit, on } from '../store.js';
import { navPush } from '../nav.js';
import { escapeHtml, h, storage, store } from '../util.js';

let root: HTMLElement;
let current: { id: string; cleanup: (() => void) | null } | null = null;
let last: string | null = null;
const OPEN_KEY = 'zc.page.open';

export const pageOpen = (): boolean => document.body.dataset.workspace === 'page';
/** The page shown, or the one shown last (what the first tab goes back to). */
export const currentPageId = (): string | null => (current?.id ?? (last && pages.get(last) ? last : null));

function teardown(): void {
  if (current?.cleanup) {
    try { current.cleanup(); } catch (e) { console.error(e); }
  }
  current = null;
  root.hidden = true;
  root.replaceChildren();
}

export function openPage(id: string, { push = true } = {}): void {
  const page = pages.get(id);
  if (!page) return;
  if (current) teardown();
  document.body.dataset.workspace = 'page';
  root.hidden = false;
  root.innerHTML = `<div class="page-head">${icon(page.icon || 'plugin')}<h2>${escapeHtml(page.title)}</h2>
      ${page.source && page.source !== page.title ? `<span class="page-source">${escapeHtml(page.source)}</span>` : ''}</div>
    <div class="page-body"${page.owner ? ` data-plugin="${escapeHtml(page.owner)}"` : ''}></div>`;
  current = { id, cleanup: null };
  last = id;
  store(OPEN_KEY, id);
  const body = root.querySelector<HTMLElement>('.page-body')!;
  try {
    const r = page.mount(body);
    if (typeof r === 'function') current.cleanup = r;
  } catch (e) {
    body.innerHTML = `<div class="empty-state">${icon('alert')}<h3>This page could not be shown</h3><p>${escapeHtml((e as Error).message)}</p></div>`;
  }
  emit('workspace');
  if (push) navPush({ ws: 'page', page: id });
}

export function closePage(): void {
  if (!pageOpen()) return;
  delete document.body.dataset.workspace;
  teardown();
  store(OPEN_KEY, null);
  emit('workspace');
  emit('focus-list');
}

export function initPages(): void {
  root = h('<div id="page-view" hidden></div>');
  document.getElementById('app')!.appendChild(root);
  on('workspace', () => {
    // Notes or Tasks took over
    if (!pageOpen() && current) { teardown(); store(OPEN_KEY, null); }
  });
  on('open-page', (id) => openPage(id));
  pages.subscribe(() => {
    if (current && !pages.get(current.id)) {
      // its plugin stopped: back to the library
      if (pageOpen()) closePage(); else teardown();
    } else if (!current && !document.body.dataset.workspace) {
      // the page that was open before a reload, once its plugin has registered it again
      const wanted = storage<string | null>(OPEN_KEY, null);
      if (wanted && pages.get(wanted)) openPage(wanted, { push: false });
    }
  });
}
