// The items table: selection, keyboard navigation, dragging items out, file drops and copying
// BibTeX. The header is in itemlist/columns.ts, the (virtualised) rows in itemlist/rows.ts and
// the context menus in itemlist/menus.ts.
import * as A from '../actions.js';
import { api, openAttachment } from '../api.js';
import { byId, currentFolderId, emit, on, selectedItems, state } from '../store.js';
import { closeMenu } from '../ui/menu.js';
import { toast, toastError } from '../ui/toast.js';
import { copyText, debounce, modKey, plural } from '../util.js';
import { initColumns, renderHead, visibleCols } from './itemlist/columns.js';
import { attachmentMenu, copyKeys, itemMenu, openItemNote } from './itemlist/menus.js';
import { initRows, order, renderBody, toggleExpand } from './itemlist/rows.js';
import { editTagsDialog } from './organizeDialogs.js';
import { initSourceVariants } from './sourceVariants.js';

export { openColumnPicker } from './itemlist/columns.js';

const ITEMS_MIME = 'application/x-zc-items';   // also read by the sidebar drop targets

let pane: HTMLElement, scroller: HTMLElement, body: HTMLElement;
let pendingCollapse: string | null = null;  // multi-selection row pressed: collapse on click, keep on drag

export function initItemList(): void {
  pane = document.getElementById('items-pane')!;
  pane.innerHTML = `<aside class="source-variants-pane" aria-label="Source dates" hidden></aside>
    <div class="items-scroll" tabindex="0" aria-label="Items">
      <div class="items-table"><div class="items-head" role="row"></div><div class="items-body" role="rowgroup"></div></div>
    </div>`;
  initSourceVariants(pane.querySelector<HTMLElement>('.source-variants-pane')!);
  scroller = pane.querySelector<HTMLElement>('.items-scroll')!;
  const table = pane.querySelector<HTMLElement>('.items-table')!;
  const head = pane.querySelector<HTMLElement>('.items-head')!;
  body = pane.querySelector<HTMLElement>('.items-body')!;

  initColumns({ table, head, body }, renderBody);
  initRows({ pane, scroller, head, body });
  bindBody();
  bindKeyboard();
  bindDrops();
  bindCopy();

  on('columns', () => { renderHead(); renderBody(); });
  on('view', renderHead);   // a plugin's list has its own sort
  on('focus-list', () => scroller.focus());
  // Which items have notes (the pencil column); refreshed whenever the notes change.
  const loadNoteKeys = debounce(async () => {
    if (state.settings.notes_enabled === false) { state.noteKeys = {}; return; }
    try { state.noteKeys = (await api.noteKeys()).keys; } catch { return; }
    if (visibleCols().some((c) => c.id === 'notes')) renderBody();
  }, 400);
  on('notes-count', () => loadNoteKeys());
  on('workspace', () => loadNoteKeys());
}

/* ============================================================ interaction */
function bindBody(): void {
  const at = (e: Event, sel: string) => (e.target as Element).closest<HTMLElement>(sel);
  // drag rows onto sidebar folders / tags / Unfiled / Trash
  body.addEventListener('dragstart', (e) => {
    const row = at(e, '.row[data-id]');
    if (!row || !e.dataTransfer) return;
    pendingCollapse = null;
    const id = row.dataset.id!;
    if (!state.selection.has(id)) A.select([id], { anchor: id, cursor: id });
    const ids = order.filter((x) => state.selection.has(x) && !byId(x)?._outside);   // papers outside the library stay put
    if (!ids.length) { e.preventDefault(); return; }
    e.dataTransfer.setData(ITEMS_MIME, JSON.stringify(ids));
    e.dataTransfer.setData('text/plain', ids.map((x) => byId(x)?.title || x).join('\n'));
    e.dataTransfer.effectAllowed = 'copyMove';
    const ghost = document.createElement('div');
    ghost.className = 'drag-ghost';
    ghost.textContent = ids.length === 1 ? (byId(ids[0])?.title || 'Item') : `${ids.length} items`;
    document.body.appendChild(ghost);
    e.dataTransfer.setDragImage(ghost, 12, 12);
    setTimeout(() => ghost.remove(), 0);
  });

  body.addEventListener('mousedown', (e) => {
    const row = at(e, '.row');
    if (!row || e.button !== 0) return;
    if (row.dataset.att || row.classList.contains('snippet')) return;
    const id = row.dataset.id!;
    if (at(e, '[data-act="toggle"]')) { toggleExpand(id); return; }
    if (at(e, '[data-act="add"]')) return;   // the "+" of a paper outside the library: its click adds it
    if (e.shiftKey && state.anchor) {
      const a = order.indexOf(state.anchor), b = order.indexOf(id);
      const [s, t] = a < b ? [a, b] : [b, a];
      A.select(order.slice(s, t + 1), { cursor: id });
    } else if (modKey(e)) {
      const sel = new Set(state.selection);
      if (sel.has(id)) sel.delete(id); else sel.add(id);
      A.select([...sel], { anchor: id, cursor: id });
    } else if (!state.selection.has(id)) {
      A.select([id], { anchor: id, cursor: id });
    } else if (state.selection.size > 1) {
      // keep the multi-selection so it can be dragged; collapse on a plain click instead
      pendingCollapse = id;
    }
  });
  body.addEventListener('click', (e) => {
    if (pendingCollapse && at(e, `.row[data-id="${CSS.escape(pendingCollapse)}"]`)) {
      A.select([pendingCollapse], { anchor: pendingCollapse, cursor: pendingCollapse });
    }
    pendingCollapse = null;
    const child = at(e, '.row.child');
    if (child) {
      const it = byId(child.dataset.parent!);
      const att = it?.attachments?.find((a) => String(a.id) === child.dataset.att);
      if (it && att) openAttachment(it, att);
      return;
    }
    const add = at(e, '[data-act="add"]');
    if (add) { const it = byId(at(e, '.row')!.dataset.id!); if (it) A.addOutside([it]); return; }
    if (at(e, '[data-act="open"]')) A.openPdf(at(e, '.row')!.dataset.id!);
    if (at(e, '[data-act="note"]')) openItemNote(byId(at(e, '.row')!.dataset.id!));
    const snip = at(e, '.row.snippet');
    if (snip) A.openPdf(snip.dataset.parent!);
    if (matchMedia('(max-width: 700px)').matches && at(e, '.row[data-id]')) document.body.classList.add('mobile-item-open');
  });
  body.addEventListener('dblclick', (e) => {
    const row = at(e, '.row[data-id]');
    if (!row || at(e, '[data-act]')) return;
    if (state.settings.open_pdf_on_double_click !== false || byId(row.dataset.id!)?._outside) A.openPdf(row.dataset.id!);   // outside: its page online
    else emit('focus-field', { id: row.dataset.id!, field: 'title' });
  });
  body.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const child = at(e, '.row.child');
    if (child) { attachmentMenu(byId(child.dataset.parent!), child.dataset.att, { x: e.clientX, y: e.clientY }); return; }
    const row = at(e, '.row[data-id]');
    if (!row) return;
    if (!state.selection.has(row.dataset.id!)) A.select([row.dataset.id!], { anchor: row.dataset.id });
    itemMenu({ x: e.clientX, y: e.clientY });
  });
  scroller.addEventListener('mousedown', (e) => {
    if (e.target === scroller || e.target === body || at(e, '.list-empty')) {
      if (!at(e, '.list-empty button')) A.select([]);
    }
  });
}

/* ============================================================== keyboard */
function bindKeyboard(): void {
  scroller.addEventListener('keydown', (e) => {
    if (e.target !== scroller) return;
    const idx = state.cursor ? order.indexOf(state.cursor) : -1;
    const move = (to: number, extend: boolean) => {
      to = Math.max(0, Math.min(order.length - 1, to));
      const id = order[to];
      if (!id) return;
      if (extend && state.anchor) {
        const a = order.indexOf(state.anchor);
        const [s, t] = a < to ? [a, to] : [to, a];
        A.select(order.slice(s, t + 1), { cursor: id });
      } else A.select([id], { anchor: id, cursor: id });
    };
    const page = Math.max(1, Math.floor(scroller.clientHeight / (body.querySelector<HTMLElement>('.row')?.offsetHeight || 28)) - 1);
    const folder = currentFolderId();
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); move(idx < 0 ? 0 : idx + 1, e.shiftKey); break;
      case 'ArrowUp': e.preventDefault(); move(idx < 0 ? 0 : idx - 1, e.shiftKey); break;
      case 'PageDown': e.preventDefault(); move(idx + page, e.shiftKey); break;
      case 'PageUp': e.preventDefault(); move(idx - page, e.shiftKey); break;
      case 'Home': e.preventDefault(); move(0, e.shiftKey); break;
      case 'End': e.preventDefault(); move(order.length - 1, e.shiftKey); break;
      case 'ArrowRight': if (state.cursor) { e.preventDefault(); toggleExpand(state.cursor, true); } break;
      case 'ArrowLeft': if (state.cursor) { e.preventDefault(); toggleExpand(state.cursor, false); } break;
      case 'Enter': if (state.cursor) { e.preventDefault(); A.openPdf(state.cursor); } break;
      case 'Delete': case 'Backspace':
        if (!state.selection.size) break;
        e.preventDefault();
        // inside a folder, Delete takes items out of the folder; Cmd/Ctrl+Delete moves them to the Trash
        if (folder && !modKey(e)) A.removeFromFolder([...state.selection], folder);
        else A.trashSelected();
        break;
      case 'a': case 'A': if (modKey(e)) { e.preventDefault(); A.select(order, { anchor: order[0], cursor: state.cursor }); } break;
      case 'c': case 'C':
        // Cmd+Shift+C: citation keys. Plain Cmd+C is handled by the 'copy' event (BibTeX), see bindCopy().
        if (modKey(e) && e.shiftKey && state.selection.size) { e.preventDefault(); copyKeys(selectedItems()); }
        break;
      case 't': case 'T': if (modKey(e) && state.selection.size && state.view !== 'trash' && !selectedItems().some((i) => i._outside)) { e.preventDefault(); editTagsDialog(); } break;
      case 'ContextMenu': {
        e.preventDefault();
        const row = body.querySelector('.row.selected');
        if (row) { const r = row.getBoundingClientRect(); itemMenu({ x: r.left + 40, y: r.bottom }); }
        break;
      }
      default: break;
    }
  });
}

/* ================================================================= drops */
function bindDrops(): void {
  let depth = 0, overRow: HTMLElement | null = null;
  const isFiles = (e: DragEvent) => [...(e.dataTransfer?.types || [])].includes('Files');
  const clearRow = () => { overRow?.classList.remove('selected'); overRow = null; };
  pane.addEventListener('dragenter', (e) => { if (!isFiles(e)) return; depth++; pane.classList.add('drag-over'); });
  pane.addEventListener('dragleave', (e) => { if (!isFiles(e)) return; if (--depth <= 0) { depth = 0; pane.classList.remove('drag-over'); clearRow(); } });
  pane.addEventListener('dragover', (e) => {
    if (!isFiles(e)) return;
    e.preventDefault();
    const row = (e.target as Element).closest<HTMLElement>('.row[data-id]');
    if (row !== overRow) {
      clearRow();
      if (row && state.view === 'library') { overRow = row; row.classList.add('selected'); pane.classList.remove('drag-over'); } else pane.classList.add('drag-over');
    }
  });
  pane.addEventListener('drop', (e) => {
    if (!isFiles(e) || !e.dataTransfer) return;
    e.preventDefault();
    depth = 0; pane.classList.remove('drag-over');
    const files = e.dataTransfer.files;
    const target = overRow?.dataset.id;
    clearRow();
    const refs = [...files].filter((f) => /\.(bib|ris|enw|xml|json|txt|nbib)$/i.test(f.name));
    if (refs.length) { emit('open-import', refs[0]); return; }
    if (target) A.uploadAttachments(target, files);
    else A.addPdfsAsItems(files);
  });
}

export function focusList(): void { scroller?.focus(); closeMenu(); }

/* ========================================================== copy BibTeX */
// Cmd/Ctrl+C on selected items copies their BibTeX. The text is prefetched
// whenever the selection changes so the 'copy' event can fill the clipboard
// synchronously (the most reliable way); if it isn't ready yet we fall back
// to the async clipboard API.
let bibCache = { key: '', text: '' };
/** The selected library items (BibTeX is only for those; papers from a plugin's list are left out). */
const selectedLibrary = () => order.filter((x) => state.selection.has(x) && !byId(x)?._outside);
const selectionKey = () => selectedLibrary().sort().join(',');

const prefetchBibtex = debounce(async () => {
  const key = selectionKey();
  if (!key || state.view === 'trash' || key === bibCache.key) return;
  try {
    const res = await api.clipboardBibtex(selectedLibrary());
    if (key === selectionKey()) bibCache = { key, text: res.text };
  } catch { /* copy will fetch on demand */ }
}, 150);

function bindCopy(): void {
  on('selection', () => prefetchBibtex());
  on('items', () => { bibCache = { key: '', text: '' }; prefetchBibtex(); });   // edits change the BibTeX
  document.addEventListener('copy', (e) => {
    // only when the list has focus and the user isn't copying selected text
    if (!scroller.contains(document.activeElement) || !selectedLibrary().length || state.view === 'trash') return;
    const sel = getSelection();
    if (sel && !sel.isCollapsed && String(sel).trim()) return;
    e.preventDefault();
    const n = selectedLibrary().length;
    const done = () => toast(`Copied BibTeX for ${plural(n, 'item')}`, { type: 'success', timeout: 2500 });
    if (bibCache.key === selectionKey() && bibCache.text && e.clipboardData) {
      e.clipboardData.setData('text/plain', bibCache.text);
      done();
    } else {
      api.clipboardBibtex(selectedLibrary())
        .then((res) => copyText(res.text)).then(done).catch(toastError);
    }
  });
}
