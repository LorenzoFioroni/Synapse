// The Library tab on a phone: search, folders as chips, the papers as rows; a paper's own page
// (what to do with it, its abstract, the notes that cite it, its folders and tags, what plugins
// add) and its details and references.
import * as A from '../actions.js';
import { api, type ApiError } from '../api.js';
import { itemActions, itemSections } from '../extend.js';
import { icon } from '../icons.js';
import { emit, fieldText, folderById, folderTree, itemTypeLabel, off, on, sortedItems, state, saveSort } from '../store.js';
import type { Attachment, Item, ItemFieldValue, ItemNotes } from '../types.js';
import { promptDialog, confirmDialog } from '../ui/dialog.js';
import { type MenuEntry, openMenu } from '../ui/menu.js';
import { toast, toastError } from '../ui/toast.js';
import { copyText, debounce, escapeHtml as esc, storage, store } from '../util.js';
import { openExport } from '../views/exportDialog.js';
import { copyBibtex, copyKeys, deleteAttachment, itemMenu, pickFiles, renameAttachment } from '../views/itemlist/menus.js';
import { openMetadataReview } from '../views/metadataReview.js';
import { addSelectionToFolder, editTagsDialog, pickFolder } from '../views/organizeDialogs.js';
import { fetchReferencesBulk } from '../views/referencesSection.js';
import { familyNames } from './model.js';
import { readerScreen } from './reader.js';
import { type Screen, back, push } from './shell.js';
import { chevron, navBar, navButton, rootHeader, roundButton, rowHtml } from './ui.js';
import { ensureNoteLib, noteScreen } from './notes.js';
import { ws } from '../views/notes/workspace.js';

const SORTS: [string, string, 'asc' | 'desc'][] = [
  ['date_added', 'Recently added', 'desc'], ['date_modified', 'Recently changed', 'desc'],
  ['title', 'Title', 'asc'], ['authors', 'Authors', 'asc'], ['year', 'Year', 'desc'],
];
const sortCol = (key: string) => (key === 'authors' ? 'creator' : key);

function viewTitle(view: string): string {
  if (view === 'library') return 'Library';
  if (view === 'trash') return 'Trash';
  if (view === 'duplicates') return 'Duplicate Items';
  if (view === 'unfiled') return 'Unfiled Items';
  if (view.startsWith('folder:')) return folderById(view.slice(7))?.name || 'Folder';
  if (view.startsWith('smart:')) return state.sidebar.smart.find((s) => String(s.id) === view.slice(6))?.name || 'Smart Folder';
  return 'Library';
}

const marksOf = (it: Item) => [
  it.attachments?.length ? `<span class="ph-mark pdf" title="Has a PDF">${icon('pdf')}</span>` : '',
  state.noteKeys[String(it.citation_key)] ? `<span class="ph-mark" title="In your notes">${icon('edit')}</span>` : '',
].join('');

/* ================================================================ the list */
export function libraryScreen(): Screen {
  return {
    title: 'Library',
    key: 'library',
    mount(el) {
      el.innerHTML = `<div class="ph-scroll">
        <div class="ph-head-slot"></div>
        <label class="ph-search">${icon('search')}<input type="search" placeholder="Titles, authors, full text, notes" aria-label="Search the library" autocomplete="off" spellcheck="false"></label>
        <div class="ph-chips" role="list"></div>
        <div class="ph-listbar">
          <button class="ph-link-btn" data-act="sort"></button>
          <button class="ph-link-btn accent" data-act="folders">${icon('folder')}Folders &amp; tags</button>
        </div>
        <div class="ph-group ph-list" role="list"></div>
        <div class="ph-more" hidden></div>
      </div>`;
      const list = el.querySelector<HTMLElement>('.ph-list')!;
      const input = el.querySelector<HTMLInputElement>('input')!;
      input.value = state.query;
      let shown = 0;
      let rows: Item[] = [];
      const PAGE = 80;
      const paintHead = () => {
        el.querySelector('.ph-head-slot')!.innerHTML = rootHeader(viewTitle(state.view), {
          kicker: state.view === 'library' ? '' : 'Library',
          actions: roundButton('gear', 'Settings', 'data-act="settings"'),
        });
        const sort = SORTS.find((s) => sortCol(s[0]) === state.sort.col || s[0] === state.sort.col);
        el.querySelector('[data-act="sort"]')!.innerHTML = `${esc(sort?.[1] || 'Sorted')}${icon('chevronDown')}`;
      };
      const paintChips = () => {
        const chip = (view: string, label: string, extra = '') => `<button class="ph-chip${state.view === view ? ' on' : ''}" data-view="${esc(view)}" role="listitem">${extra}${esc(label)}</button>`;
        el.querySelector('.ph-chips')!.innerHTML = [
          chip('library', `All ${state.counts.library ?? ''}`.trim()),
          ...folderTree().filter((f) => f.depth === 0 || state.view === `folder:${f.id}`).map((f) => chip(`folder:${f.id}`, f.name, icon('folder'))),
          ...state.sidebar.smart.map((s) => chip(`smart:${s.id}`, s.name, icon('smartFolder'))),
          ...[...state.tagFilter].map((t) => `<button class="ph-chip on tag" data-tag="${esc(t)}">#${esc(t)} ${icon('close')}</button>`),
        ].join('');
        // the chosen chip in view (sideways only: the page itself stays where it is)
        const bar = el.querySelector<HTMLElement>('.ph-chips')!, on = bar.querySelector<HTMLElement>('.ph-chip.on[data-view]');
        if (on && (on.offsetLeft < bar.scrollLeft || on.offsetLeft + on.offsetWidth > bar.scrollLeft + bar.clientWidth)) bar.scrollLeft = on.offsetLeft - 16;
      };
      const more = () => {
        const next = rows.slice(shown, shown + PAGE);
        list.insertAdjacentHTML('beforeend', next.map((it) => rowHtml(it, { marks: marksOf(it) })).join(''));
        shown += next.length;
        el.querySelector<HTMLElement>('.ph-more')!.hidden = shown >= rows.length;
      };
      const paintList = () => {
        rows = sortedItems();
        shown = 0;
        list.innerHTML = '';
        if (!rows.length) {
          list.innerHTML = `<div class="ph-empty">${icon(state.query ? 'search' : 'library')}<h3>${state.loading ? 'Loading…' : state.query ? 'Nothing found' : 'Nothing here yet'}</h3>
            <p>${state.query ? 'Try other words, or search the full text from the menu of the search field.' : 'Add papers with the + button.'}</p></div>`;
          el.querySelector<HTMLElement>('.ph-more')!.hidden = true;
          return;
        }
        more();
      };
      const paint = () => { paintHead(); paintChips(); paintList(); };
      const scroller = el.querySelector<HTMLElement>('.ph-scroll')!;
      scroller.addEventListener('scroll', () => {
        if (shown < rows.length && scroller.scrollTop + scroller.clientHeight > scroller.scrollHeight - 600) more();
      }, { passive: true });
      const search = debounce(() => { A.setSearch(input.value.trim()); void A.loadItems({ keepSelection: false }); }, 300);
      input.addEventListener('input', search);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') input.blur(); });
      el.addEventListener('click', (e) => {
        const t = e.target as Element;
        const row = t.closest<HTMLElement>('.ph-row[data-id]');
        if (row) { push(itemScreen(row.dataset.id!)); return; }
        const chip = t.closest<HTMLElement>('.ph-chip[data-view]');
        if (chip) { A.setView(chip.dataset.view!); void A.loadItems({ keepSelection: false }); return; }
        const tag = t.closest<HTMLElement>('.ph-chip[data-tag]');
        if (tag) { A.toggleTagFilter(tag.dataset.tag!); return; }
        const act = t.closest<HTMLElement>('[data-act]')?.dataset.act;
        if (act === 'settings') void import('./main.js').then((m) => m.settingsMenu());
        if (act === 'sort') sortMenu();
        if (act === 'folders') foldersMenu();
      });
      // a long press on a row: the item menu (as on the desktop)
      el.addEventListener('contextmenu', (e) => {
        const row = (e.target as Element).closest<HTMLElement>('.ph-row[data-id]');
        if (!row) return;
        e.preventDefault();
        A.select([row.dataset.id!], { anchor: row.dataset.id!, cursor: row.dataset.id! });
        itemMenu({ x: 0, y: innerHeight });
      });
      const onItems = () => paint();
      const onLoading = () => { if (!rows.length) paintList(); };
      on('items', onItems); on('sidebar', paintChips); on('view', paint); on('loading', onLoading); on('tag-filter', paint);
      paint();
      void loadNoteKeys().then(() => { if (rows.length) { shown = 0; list.innerHTML = ''; more(); } });
      return () => { off('items', onItems); off('sidebar', paintChips); off('view', paint); off('loading', onLoading); off('tag-filter', paint); };
    },
  };
}

/** Which papers have notes (the pencil on rows). */
async function loadNoteKeys(): Promise<void> {
  if (state.settings.notes_enabled === false) return;
  try { state.noteKeys = (await api.noteKeys()).keys; } catch { /* rows just show no pencil */ }
}

function sortMenu(): void {
  openMenu([
    { section: 'Sort by' },
    ...SORTS.map(([key, label, dir]) => ({
      label, checked: state.sort.col === sortCol(key),
      run: () => {
        const col = sortCol(key);
        state.sort = state.sort.col === col ? { col, dir: state.sort.dir === 'asc' ? 'desc' : 'asc' } : { col, dir };
        saveSort();
        emit('items');
      },
    })),
  ], { x: 0, y: innerHeight });
}

function foldersMenu(): void {
  const go = (view: string) => { A.setView(view); void A.loadItems({ keepSelection: false }); };
  openMenu([
    { label: `My Library`, icon: 'library', checked: state.view === 'library', run: () => go('library') },
    { label: 'Unfiled Items', icon: 'unfiled', checked: state.view === 'unfiled', run: () => go('unfiled') },
    { label: 'Duplicate Items', icon: 'copy', checked: state.view === 'duplicates', run: () => go('duplicates') },
    { label: `Trash${state.counts.trash ? ` (${state.counts.trash})` : ''}`, icon: 'trash', checked: state.view === 'trash', run: () => go('trash') },
    folderTree().length ? { section: 'Folders' } : null,
    ...folderTree().map((f) => ({ label: `${'  '.repeat(f.depth)}${f.name}`, checked: state.view === `folder:${f.id}`, run: () => go(`folder:${f.id}`) })),
    state.sidebar.smart.length ? { section: 'Smart folders' } : null,
    ...state.sidebar.smart.map((s) => ({ label: s.name, checked: state.view === `smart:${s.id}`, run: () => go(`smart:${s.id}`) })),
    state.sidebar.tags.length ? { section: 'Show only papers tagged' } : null,
    ...state.sidebar.tags.map((t) => ({ label: `#${t.name}`, checked: [...state.tagFilter].some((x) => x.toLowerCase() === t.name.toLowerCase()), run: () => A.toggleTagFilter(t.name) })),
    state.view === 'trash' && state.counts.trash ? 'sep' : null,
    state.view === 'trash' && state.counts.trash ? { label: 'Empty Trash…', icon: 'trash', danger: true, run: () => A.emptyTrash() } : null,
  ], { x: 0, y: innerHeight });
}

/* ================================================================ a paper */
const fullName = (a: string) => { const [fam, given] = a.split(',').map((s) => s.trim()); return given ? `${given} ${fam}` : fam; };
const authorsLine = (it: Item) => String(it.authors || '').split(';').map((a) => a.trim()).filter(Boolean).map(fullName).join(', ');
const shortName = (it: Item) => [familyNames(it.authors), it.year ? String(it.year) : ''].filter(Boolean).join(' ') || 'Paper';

/** Where a paper was published, as one line: journal volume, pages · year. */
function whereLine(it: Item): string {
  const journal = [it.journal, it.volume, it.pages].filter(Boolean).join(', ');
  return [journal, it.year ? String(it.year) : ''].filter(Boolean).join(' · ') || (it.arxiv ? `arXiv:${it.arxiv}` : '');
}

/** The dismiss action a plugin offers for these papers (role 'dismiss'), if any. */
export function dismissAction(items: Item[]) {
  return itemActions.all().find((a) => a.role === 'dismiss' && (() => { try { return !a.when || a.when(items); } catch { return false; } })());
}

/** What plugins offer for these papers (their item actions). */
export function pluginEntries(items: Item[]): MenuEntry[] {
  const usable = itemActions.all().filter((a) => { try { return !a.when || a.when(items); } catch { return false; } });
  return usable.length ? [{ section: 'From plugins' }, ...usable.map((a) => ({ label: a.title, icon: a.icon || 'plugin', run: () => a.run(items) }))] : [];
}

/** Open a paper's PDF on the phone (choosing one when it has several). */
export function readPdf(it: Item, page: number | null = null): void {
  const pdfs = (it.attachments || []) as Attachment[];
  if (pdfs.length === 1 || (pdfs.length && page)) { push(readerScreen(it, pdfs[0], page)); return; }
  if (pdfs.length > 1) {
    openMenu([{ section: 'Open which PDF?' }, ...pdfs.map((a) => ({ label: a.name || 'PDF', icon: 'pdf', run: () => push(readerScreen(it, a, page)) }))], { x: 0, y: innerHeight });
    return;
  }
  if (it._outside && it.arxiv) { window.open(`https://arxiv.org/pdf/${encodeURIComponent(it.arxiv)}`, '_blank', 'noopener'); return; }
  if (it._outside && it.url) { window.open(it.url, '_blank', 'noopener'); return; }
  toast('This paper has no PDF yet. Find one from its ⋯ menu.');
}

/**
 * A paper's page. ``given`` is its id (a library paper, fetched whole) or the paper itself (one a
 * plugin lists, maybe not in the library).
 */
export function itemScreen(given: string | Item): Screen {
  const id = typeof given === 'string' ? given : given.id;
  return {
    title: 'Paper',
    key: `item:${id}`,
    async mount(el, ctx) {
      el.innerHTML = `${navBar(ctx.backLabel, '', navButton('more', 'More actions', 'data-act="more"'))}<div class="ph-scroll ph-item"><div class="ph-loading">Loading…</div></div>`;
      let it: Item;
      const load = async () => {
        if (typeof given !== 'string' && given._outside) return given;
        const full = await api.getItem(id);
        // keep why a plugin's list showed it
        if (typeof given !== 'string') Object.assign(full, { reasons: given.reasons, highlights: given.highlights, _source: given._source, _group: given._group });
        return full;
      };
      try { it = await load(); } catch (e) {
        el.querySelector('.ph-item')!.innerHTML = `<div class="ph-empty">${icon('alert')}<h3>Not found</h3><p>${esc((e as ApiError).message)}</p></div>`;
        return;
      }
      const body = el.querySelector<HTMLElement>('.ph-item')!;
      const sections: (() => void)[] = [];
      const paint = () => {
        for (const c of sections.splice(0)) { try { c(); } catch (e) { console.error(e); } }
        el.querySelector('.ph-nav-title')!.textContent = shortName(it);
        body.innerHTML = itemHtml(it);
        void fillNotes(body, it);
        void fillPlugins(body, it, sections);
      };
      paint();
      el.addEventListener('click', (e) => onItemClick(e, it, el));
      // the paper changed (edited, added to a folder, a PDF found): show it again
      const refresh = debounce(async () => {
        if (it._outside) return;
        try { const now = await api.getItem(id); if (JSON.stringify(now) !== JSON.stringify(it)) { it = { ...now, reasons: it.reasons, highlights: it.highlights }; paint(); } } catch { /* gone: stays as it was */ }
      }, 400);
      const onUpdated = (changed: string) => { if (changed === id) refresh(); };
      on('items', refresh); on('item-updated', onUpdated); on('item-fields', refresh);
      return () => { off('items', refresh); off('item-updated', onUpdated); off('item-fields', refresh); for (const c of sections) c(); };
    },
  };
}

function itemHtml(it: Item): string {
  const outside = !!it._outside;
  const notesOn = state.settings.notes_enabled !== false;
  const hasPdf = !!it.attachments?.length;
  const dismiss = outside || it._source ? dismissAction([it]) : undefined;
  const actions = outside
    ? `<div class="ph-primary-row">
        <button class="ph-btn primary grow" data-act="add">${icon('plus')}Add to Library</button>
        <button class="ph-btn square" data-act="add-folder" aria-label="Add to a folder" title="Add to a folder">${icon('folderPlus')}</button></div>
       <div class="ph-primary-row">
        <button class="ph-btn grow" data-act="read">${icon('pdf')}Read PDF</button>
        ${dismiss ? '<button class="ph-btn grow" data-act="dismiss">Not for me</button>' : ''}</div>`
    : `<div class="ph-action-grid">
        <button class="ph-action${hasPdf ? ' primary' : ''}" data-act="${hasPdf ? 'read' : 'find-pdf'}">${icon(hasPdf ? 'pdf' : 'find')}<span>${hasPdf ? 'Read' : 'Find PDF'}</span></button>
        ${notesOn ? `<button class="ph-action" data-act="note">${icon('edit')}<span>${state.noteKeys[String(it.citation_key)]?.paper ? 'Paper note' : 'New note'}</span></button>` : ''}
        <button class="ph-action" data-act="copy-key">${icon('key')}<span>Copy key</span></button>
        <button class="ph-action" data-act="share">${icon('export')}<span>Share</span></button>
      </div>`;
  const reasons = (it.reasons || []).filter((r) => r.text);
  const chips = [
    ...(it.folder_ids || []).map((f) => folderById(f)).filter(Boolean).map((f) => `<span class="ph-tag">${icon('folder')}${esc(f!.name)}</span>`),
    ...(it.tags || []).map((t) => `<span class="ph-tag">#${esc(t)}</span>`),
  ];
  return `
    <div class="ph-item-head">
      <div class="ph-kicker">${esc(itemTypeLabel(it.item_type))}${it.deleted_at ? ' · in the Trash' : ''}</div>
      ${badgesRow(it)}
      <h1>${esc(it.title || 'Untitled')}</h1>
      ${it.authors ? `<p class="ph-authors">${esc(authorsLine(it))}</p>` : ''}
      <p class="ph-where">${esc(whereLine(it))}</p>
    </div>
    ${it.deleted_at ? `<div class="ph-primary-row"><button class="ph-btn primary grow" data-act="restore">${icon('restore')}Restore to Library</button></div>` : actions}
    ${reasons.length ? `<section class="ph-card-box"><h2>Why it’s here</h2><ul class="ph-reasons">${reasons.map((r) => `<li>${esc(r.text)}</li>`).join('')}</ul></section>` : ''}
    ${it.abstract ? `<section class="ph-card-box"><h2>Abstract</h2><p class="ph-abstract clamp" data-act="abstract">${esc(it.abstract)}</p></section>` : ''}
    ${!outside && notesOn && it.citation_key ? '<section class="ph-card-box ph-notes-box" hidden></section>' : ''}
    ${outside ? '' : `<section class="ph-card-box flush">
      <div class="ph-tags">${chips.join('')}${chips.length
        ? `<button class="ph-tag add" data-act="organize" aria-label="Add to a folder or tag">${icon('plus')}</button>`
        : `<button class="ph-tag add wide" data-act="organize">${icon('plus')}Add to a folder or tag</button>`}</div>
      ${(it.attachments || []).map((a) => `<div class="ph-attachment">
        <button class="ph-link-row" data-att="${a.id}">${icon('pdf')}<span>${esc(a.name || 'PDF')}</span>${chevron()}</button>
        ${it.deleted_at ? '' : navButton('more', `Actions for ${a.name || 'PDF'}`, `data-att-menu="${a.id}" aria-haspopup="menu"`)}
      </div>`).join('')}
      <button class="ph-link-row" data-act="refs">${icon('link')}<span>References and citations</span>${chevron()}</button>
      <button class="ph-link-row" data-act="details">${icon('info')}<span>All details <small>${esc(it.citation_key || '')}</small></span>${chevron()}</button>
    </section>`}
    <div class="ph-plugin-slot"></div>`;
}

function badgesRow(it: Item): string {
  const list = it.highlights?.length ? it.highlights : it.highlight ? [it.highlight] : [];
  if (!list.length) return '';
  return `<div class="ph-badges">${list.map((b) => `<span class="ph-badge tone-${esc(b.tone || 'gold')}" title="${esc(b.text)}">${b.icon ? icon(b.icon) : ''}${esc(b.label)}</span>`).join('')}</div>`;
}

/** "In your notes": its paper note and the notes that cite it. */
async function fillNotes(body: HTMLElement, it: Item): Promise<void> {
  const box = body.querySelector<HTMLElement>('.ph-notes-box');
  if (!box) return;
  let res: ItemNotes;
  try { [res] = await Promise.all([api.itemNotes(it.id), ensureNoteLib()]); } catch { return; }
  if (!box.isConnected) return;
  const inline = (md: string) => (ws.renderer ? ws.renderer.renderInline(md) : esc(md));
  const rows = [
    ...res.paper_notes.map((n) => `<button class="ph-note-ref" data-note="${esc(n.path)}"><strong>${esc(n.title || n.path)}</strong><small>Paper note</small></button>`),
    ...res.citing.slice(0, 6).map((c) => `<button class="ph-note-ref" data-note="${esc(c.path)}" data-line="${c.line}"><strong>${esc(c.source_title || c.path)}</strong>
      <small>${inline(c.context.length > 160 ? `${c.context.slice(0, 160)}…` : c.context)}</small></button>`),
  ];
  if (!rows.length) return;
  box.hidden = false;
  box.innerHTML = `<h2>In your notes</h2>${rows.join('')}${res.citing.length > 6 ? `<p class="ph-muted">and ${res.citing.length - 6} more</p>` : ''}`;
}

/** "From plugins": the values they attach (Citations, Metadata …) and their own sections. */
async function fillPlugins(body: HTMLElement, it: Item, cleanups: (() => void)[]): Promise<void> {
  const slot = body.querySelector<HTMLElement>('.ph-plugin-slot')!;
  let fields: ItemFieldValue[] = [];
  if (!it._outside) { try { fields = (await api.itemFields(it.id)).fields; } catch { /* none */ } }
  else fields = state.fieldDefs.map((d) => ({ ...d, value: it._f?.[d.id], checked: true, updated: null, source_url: null }));
  fields = fields.filter((f) => f.value !== null && f.value !== undefined && f.value !== '');
  const sections = itemSections.all().filter((s) => { try { return !s.when || s.when(it); } catch { return false; } });
  if (!slot.isConnected || (!fields.length && !sections.length)) return;
  const folded = new Set(storage<string[]>('zc.phone.folded', []));
  slot.innerHTML = `<h2 class="ph-section-label">From plugins</h2>
    ${fields.length ? `<section class="ph-card-box flush">${fields.map((f) => `<div class="ph-field-row"><span><span>${esc(f.label)}</span><small>${esc(f.plugin_name)}${f.source ? ` · ${esc(f.source)}` : ''}</small></span>
      <strong>${esc(fieldText(f, f.value))}</strong></div>`).join('')}</section>` : ''}
    ${sections.map((s) => `<section class="ph-card-box flush ph-plugin-section" data-section="${esc(s.id)}"${s.owner ? ` data-plugin="${esc(s.owner)}"` : ''}>
      <button class="ph-fold" aria-expanded="${!folded.has(s.id)}"><span><span>${esc(s.title)}</span><small>${esc(s.source || '')}</small></span>${icon('chevronDown')}</button>
      <div class="ph-plugin-body"${folded.has(s.id) ? ' hidden' : ''}></div></section>`).join('')}`;
  const mount = (s: (typeof sections)[number], box: HTMLElement) => {
    try { const r = s.mount(box, it); if (typeof r === 'function') cleanups.push(r); } catch (e) { box.textContent = `Could not be shown: ${(e as Error).message}`; }
  };
  for (const s of sections) {
    const sec = slot.querySelector<HTMLElement>(`[data-section="${CSS.escape(s.id)}"]`)!;
    const box = sec.querySelector<HTMLElement>('.ph-plugin-body')!;
    if (!box.hidden) mount(s, box);
    sec.querySelector('.ph-fold')!.addEventListener('click', (e) => {
      const b = e.currentTarget as HTMLElement;
      const open = box.hidden;
      box.hidden = !open;
      b.setAttribute('aria-expanded', String(open));
      if (open) { folded.delete(s.id); if (!box.childElementCount) mount(s, box); } else folded.add(s.id);
      store('zc.phone.folded', [...folded]);
    });
  }
}

async function onItemClick(e: MouseEvent, it: Item, el: HTMLElement): Promise<void> {
  const t = e.target as Element;
  const note = t.closest<HTMLElement>('[data-note]');
  if (note) { push(noteScreen(note.dataset.note!, { line: note.dataset.line ? Number(note.dataset.line) : null })); return; }
  const attMenu = t.closest<HTMLElement>('[data-att-menu]');
  if (attMenu) {
    const a = it.attachments?.find((x) => String(x.id) === attMenu.dataset.attMenu);
    if (a && !it.deleted_at) openMenu([
      { label: 'Rename…', icon: 'edit', run: () => renameAttachment(a) },
      'sep',
      { label: 'Delete Attachment', icon: 'trash', danger: true, run: () => deleteAttachment(a) },
    ], attMenu);
    return;
  }
  const att = t.closest<HTMLElement>('[data-att]');
  if (att) { const a = it.attachments?.find((x) => String(x.id) === att.dataset.att); if (a) push(readerScreen(it, a)); return; }
  const act = t.closest<HTMLElement>('[data-act]')?.dataset.act;
  if (!act) return;
  switch (act) {
    case 'more': itemActionsMenu(it); break;
    case 'read': readPdf(it); break;
    case 'find-pdf': await A.findPdf(it.id); break;
    case 'note': await openPaperNote(it); break;
    case 'copy-key': if (it.citation_key && await copyText(it.citation_key)) toast(`Copied ${it.citation_key}`, { type: 'success', timeout: 2000 }); break;
    case 'share': await share(it); break;
    case 'abstract': t.closest('.ph-abstract')!.classList.toggle('clamp'); break;
    case 'organize': openMenu([
      { label: 'Add to Folder…', icon: 'folderPlus', run: () => addSelectionToFolder([it.id]) },
      { label: 'Edit Tags…', icon: 'tag', run: () => editTagsDialog([it.id]) },
    ], { x: 0, y: innerHeight }); break;
    case 'refs': push(referencesScreen(it)); break;
    case 'details': push(detailsScreen(it)); break;
    case 'add': await addOutside(it, null, el); break;
    case 'add-folder': {
      const fid = await pickFolder({ title: 'Add to Folder' });
      if (fid !== undefined) await addOutside(it, fid, el);
      break;
    }
    case 'dismiss': { const d = dismissAction([it]); if (d) { await d.run([it]); back(); } break; }
    case 'restore': await api.restore([it.id]); await A.loadItems(); toast('Restored to the library', { type: 'success' }); back(); break;
  }
}

async function addOutside(it: Item, folderId: number | null, el: HTMLElement): Promise<void> {
  el.querySelectorAll<HTMLButtonElement>('[data-act="add"], [data-act="add-folder"]').forEach((b) => { b.disabled = true; });
  await A.addOutside([it], folderId);
  emit('sources');   // the list it came from shows it as in the library now
  back();
}

async function openPaperNote(it: Item): Promise<void> {
  const paper = state.noteKeys[String(it.citation_key)]?.paper;
  if (paper) { push(noteScreen(paper)); return; }
  try {
    const res = await api.createNote({ item_id: it.id });
    await loadNoteKeys();
    push(noteScreen(res.path, { edit: true }));
  } catch (e) {
    const existing = ((e as ApiError).data as { path?: string } | undefined)?.path;
    if (existing) push(noteScreen(existing)); else toastError(e);
  }
}

async function share(it: Item): Promise<void> {
  const url = it.doi ? `https://doi.org/${it.doi}` : it.url || (it.arxiv ? `https://arxiv.org/abs/${it.arxiv}` : '');
  const text = `${it.title || ''}${it.authors ? ` — ${familyNames(it.authors)}` : ''}${it.year ? ` (${it.year})` : ''}`;
  if (navigator.share) { try { await navigator.share({ title: it.title || undefined, text, url: url || undefined }); } catch { /* cancelled */ } return; }
  if (await copyText(url ? `${text}\n${url}` : text)) toast('Copied', { type: 'success', timeout: 2000 });
}

/** The ⋯ menu of a paper: what the desktop's item menu offers, as a sheet. */
function itemActionsMenu(it: Item): void {
  const ids = [it.id];
  if (it._outside) {
    const ident = it.arxiv ? `arXiv:${it.arxiv}` : it.doi || '';
    openMenu([
      { label: 'Add to My Library', icon: 'plus', run: () => A.addOutside([it]) },
      { label: 'Add to Folder…', icon: 'folderPlus', run: async () => { const f = await pickFolder({ title: 'Add to Folder' }); if (f !== undefined) await A.addOutside([it], f); } },
      'sep',
      it.url ? { label: 'View Online', icon: 'globe', run: () => window.open(it.url || '', '_blank', 'noopener') } : null,
      ident ? { label: 'Copy Identifier', icon: 'copy', run: async () => { if (await copyText(ident)) toast(`Copied ${ident}`, { type: 'success', timeout: 2000 }); } } : null,
      ...pluginEntries([it]),
    ], { x: 0, y: innerHeight });
    return;
  }
  if (it.deleted_at) {
    openMenu([
      { label: 'Restore to Library', icon: 'restore', run: async () => { await api.restore(ids); await A.loadItems(); back(); } },
      { label: 'Delete Permanently…', icon: 'trash', danger: true, run: async () => { await A.deleteForever(ids); back(); } },
    ], { x: 0, y: innerHeight });
    return;
  }
  const online = it.url || (it.doi ? `https://doi.org/${it.doi}` : '');
  openMenu([
    online ? { label: 'View Online', icon: 'globe', run: () => window.open(online, '_blank', 'noopener') } : null,
    { label: 'Retrieve Metadata…', icon: 'sparkle', run: () => openMetadataReview(ids) },
    { label: 'Find Available PDF', icon: 'find', run: () => A.findPdf(it.id) },
    { label: 'Add PDF Attachment…', icon: 'paperclip', run: () => pickFiles((files) => A.uploadAttachments(it.id, files)) },
    { label: 'Fetch References', icon: 'link', run: () => fetchReferencesBulk(ids) },
    'sep',
    { label: 'Add to Folder…', icon: 'folderPlus', run: () => addSelectionToFolder(ids) },
    { label: 'Edit Tags…', icon: 'tag', run: () => editTagsDialog(ids) },
    'sep',
    { label: 'Copy Citation Key', icon: 'key', run: () => copyKeys([it]) },
    { label: 'Copy as BibTeX', icon: 'copy', run: () => copyBibtex(ids) },
    { label: 'Export…', icon: 'export', run: () => openExport(ids) },
    ...pluginEntries([it]),
    'sep',
    { label: 'Move to Trash', icon: 'trash', danger: true, run: async () => {
      await api.trash(ids);
      await A.loadItems();
      back();
      toast('Moved to the Trash', { action: { label: 'Undo', run: async () => { await api.restore(ids); await A.loadItems(); } } });
    } },
  ], { x: 0, y: innerHeight });
}

/* ================================================================ details and references */
function detailsScreen(it: Item): Screen {
  return {
    title: 'Details',
    mount(el, ctx) {
      const paint = () => {
        const keys = state.schema?.typeFields[String(it.item_type || 'journalArticle')] || state.schema?.commonFields || [];
        const fields = (state.schema?.fields || []).filter((f) => f.editable && (keys.includes(f.key) || it[f.key]));
        el.innerHTML = `${navBar(ctx.backLabel, 'Details')}<div class="ph-scroll"><section class="ph-card-box flush ph-details">
          <div class="ph-field-row"><span>Item type</span><strong>${esc(itemTypeLabel(it.item_type))}</strong></div>
          ${fields.map((f) => `<button class="ph-field-row edit" data-field="${esc(f.key)}"><span>${esc(f.label)}</span>
            <strong class="${it[f.key] ? '' : 'empty'}">${esc(it[f.key] ?? 'Add')}</strong></button>`).join('')}
        </section><p class="ph-muted pad">Tap a field to change it.</p></div>`;
      };
      paint();
      el.addEventListener('click', async (e) => {
        const row = (e.target as Element).closest<HTMLElement>('[data-field]');
        if (!row) return;
        const key = row.dataset.field!;
        const def = state.schema?.fields.find((f) => f.key === key);
        const hint = key === 'authors' ? 'Family, Given; Family, Given' : undefined;
        const value = await promptDialog(def?.label || key, { title: 'Edit', value: String(it[key] ?? ''), confirm: 'Save', hint });
        if (value === null || value === String(it[key] ?? '')) return;
        if (key === 'citation_key' && !await confirmDialog('Notes citing the old key are updated to the new one.', { title: 'Change the citation key?', confirm: 'Change' })) return;
        try { it = await A.updateField(it.id, key, value); paint(); } catch (err) { toastError(err); }
      });
    },
  };
}

function referencesScreen(it: Item): Screen {
  return {
    title: 'References',
    async mount(el, ctx) {
      el.innerHTML = `${navBar(ctx.backLabel, 'References')}<div class="ph-scroll"><div class="ph-loading">Loading…</div></div>`;
      const box = el.querySelector<HTMLElement>('.ph-scroll')!;
      const paint = (refs: Awaited<ReturnType<typeof api.itemReferences>>) => {
        const ref = (r: typeof refs.references[number]) => {
          const meta = [familyNames(r.authors), r.year ? String(r.year) : '', r.venue || ''].filter(Boolean).join(' · ');
          return r.library_id
            ? `<button class="ph-row" data-id="${esc(r.library_id)}"><span class="ph-row-main"><span class="ph-row-title">${esc(r.title || r.raw || 'Untitled')}</span><span class="ph-row-meta">${esc(meta)}</span></span><span class="ph-row-marks"><span class="ph-mark in">${icon('check')}</span></span></button>`
            : `<div class="ph-row static"><span class="ph-row-main"><span class="ph-row-title">${esc(r.title || r.raw || 'Untitled')}</span><span class="ph-row-meta">${esc(meta || r.doi || '')}</span></span></div>`;
        };
        const mine = refs.references.filter((r) => r.library_id).length;
        box.innerHTML = `
          ${refs.cited_by.length ? `<h2 class="ph-section-label">Cited by in your library · ${refs.cited_by.length}</h2><div class="ph-group">${refs.cited_by.map((c) => `<button class="ph-row" data-id="${esc(c.id)}"><span class="ph-row-main"><span class="ph-row-title">${esc(c.title || 'Untitled')}</span><span class="ph-row-meta">${esc([familyNames(c.authors), c.year ? String(c.year) : ''].filter(Boolean).join(' · '))}</span></span></button>`).join('')}</div>` : ''}
          <h2 class="ph-section-label">References · ${refs.references.length}${mine ? `, ${mine} in your library` : ''}</h2>
          ${refs.references.length ? `<div class="ph-group">${refs.references.map(ref).join('')}</div>`
            : `<div class="ph-empty"><p>${refs.queued ? 'Being fetched…' : 'No reference list yet.'}</p>${refs.can_fetch && !refs.queued ? '<button class="ph-pill" data-act="fetch">Fetch References</button>' : ''}</div>`}`;
      };
      try { paint(await api.itemReferences(it.id)); } catch (e) { box.innerHTML = `<div class="ph-empty"><p>${esc((e as Error).message)}</p></div>`; }
      el.addEventListener('click', async (e) => {
        const row = (e.target as Element).closest<HTMLElement>('.ph-row[data-id]');
        if (row) { push(itemScreen(row.dataset.id!)); return; }
        if ((e.target as Element).closest('[data-act="fetch"]')) {
          try { paint(await api.fetchReferences(it.id)); } catch (err) { toastError(err); }
        }
      });
    },
  };
}

