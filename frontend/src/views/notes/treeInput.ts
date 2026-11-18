// Mouse and keyboard in the notes tree: clicks select and open (Shift and Cmd/Ctrl extend the
// selection), arrows move, Enter opens, F2 renames, Delete trashes; the context menu; and
// dragging notes and folders onto folders (or as [[links]] into the editor).
import { api, openNotePdf } from '../../api.js';
import { PALETTE, colorName } from '../../ui/colors.js';
import { openMenu, type MenuEntry } from '../../ui/menu.js';
import { toastError } from '../../ui/toast.js';
import { modKey } from '../../util.js';
import { openNewWindow } from '../../windows.js';
import {
  addPdfs, copyLinks, deleteSelection, describe, moveItems, newFolder, newNote, pickFolder, pickPdfs, renameFolder, renameNote, renamePdf, type Selection,
} from './fileOps.js';
import { exportMenu } from './exportNote.js';
import {
  fKey, keyOf, nKey, paintSelection, refreshTree, selectOnly, selectRange, selected, toggleFolder, toggleSelected, tree, treeEl, visibleKeys,
} from './tree.js';
import { noteHooks } from './hooks.js';
import { hooks, parentOf, stemOf, ws } from './workspace.js';

const NOTE_MIME = 'application/x-zc-note';

function onClick(e: MouseEvent): void {
  const t = e.target as Element;
  const hit = t.closest<HTMLElement>('.nt-hit');
  if (hit) { if (hit.classList.contains('pdf')) openNotePdf({ id: Number(hit.dataset.id), path: hit.dataset.path! }); else hooks.openNote(hit.dataset.path!); return; }
  const row = t.closest<HTMLElement>('.nt-row');
  if (!row) return;
  // Clicking in the tree keeps the keyboard there (arrows, Delete, F2) until you click the note.
  treeEl().focus({ preventScroll: true });
  const key = keyOf(row);
  if (e.shiftKey) { selectRange(key); paintSelection(); return; }
  if (modKey(e)) { toggleSelected(key); paintSelection(); return; }
  selectOnly(key);
  if (row.classList.contains('folder')) { toggleFolder(row.dataset.folder!); return; }
  paintSelection();
  if (row.classList.contains('pdf')) openPdfRow(row); else hooks.openNote(row.dataset.path!);
}

/** A PDF of the tree, in the viewer (``newTab``: a tab of its own). */
function openPdfRow(row: HTMLElement, newTab = false): void {
  openNotePdf({ id: Number(row.dataset.id), path: row.dataset.path! }, null, {}, { newTab });
}

/** A double-click keeps the note's tab, as in VS Code (the first click opened it in the preview
 *  tab). Renaming is F2 or the menu. */
function onDblClick(e: MouseEvent): void {
  if (e.shiftKey || modKey(e)) return;
  const note = (e.target as Element).closest<HTMLElement>('.nt-row.note, .nt-hit:not(.pdf)');
  if (note) hooks.openNote(note.dataset.path!, { keep: true });
  const pdf = (e.target as Element).closest<HTMLElement>('.nt-row.pdf');
  if (pdf) openPdfRow(pdf, true);
}

/** Keys in the file tree: arrows move (Shift extends), Enter opens, F2 renames, Delete trashes. */
function onKey(e: KeyboardEvent): void {
  if ((e.target as Element).closest('.nt-hit')) return;   // a "Text matches" button handles its own keys
  const keys = visibleKeys();
  if (!keys.length || e.altKey) return;
  const mod = modKey(e);
  const { cursor } = tree;
  const i = cursor ? keys.indexOf(cursor) : -1;
  const isFolder = !!cursor?.startsWith('f:');
  const isPdf = !!cursor?.startsWith('p:');
  const path = cursor?.slice(2) || '';
  const go = (j: number) => {
    const k = keys[Math.max(0, Math.min(keys.length - 1, j))];
    if (e.shiftKey) selectRange(k); else selectOnly(k);
    paintSelection();
    treeEl().querySelector('.nt-row.cursor')?.scrollIntoView({ block: 'nearest' });
  };
  let handled = true;
  switch (e.key) {
    case 'ArrowDown': go(i < 0 ? 0 : i + 1); break;
    case 'ArrowUp': go(i < 0 ? keys.length - 1 : i - 1); break;
    case 'Home': go(0); break;
    case 'End': go(keys.length - 1); break;
    case 'ArrowRight':
      if (isFolder && tree.collapsed.has(path) && !tree.filter) toggleFolder(path, true);
      else if (isFolder) go(i + 1);
      break;
    case 'ArrowLeft': {
      if (isFolder && !tree.collapsed.has(path) && !tree.filter) { toggleFolder(path, false); break; }
      const up = path ? keys.indexOf(fKey(parentOf(path))) : -1;
      if (up >= 0) go(up);
      break;
    }
    case 'Enter':
      if (!cursor || mod) { handled = false; break; }
      if (isFolder) toggleFolder(path);
      else if (isPdf) { const pdf = (ws.overview.pdfs || []).find((x) => x.path === path); if (pdf) openNotePdf(pdf); }
      else hooks.openNote(path).then(() => (ws.mode === 'read' ? ws.root.querySelector<HTMLElement>('.note-read')!.focus() : ws.editor?.focus()));
      break;
    case 'F2':
      if (cursor) (isFolder ? renameFolder : isPdf ? renamePdf : renameNote)(path);
      break;
    case 'Delete': case 'Backspace':
      deleteSelection();
      break;
    case 'Escape':
      if (tree.sel.size > 1 || !ws.session) selectOnly(ws.session ? nKey(ws.session.path) : null); else handled = false;
      paintSelection();
      break;
    default:
      if (mod && e.key.toLowerCase() === 'a') { tree.sel = new Set(keys); paintSelection(); } else handled = false;
  }
  if (handled) { e.preventDefault(); e.stopPropagation(); }
}

function onContextMenu(e: MouseEvent): void {
  const row = (e.target as Element).closest<HTMLElement>('.nt-row');
  e.preventDefault();
  const at = { x: e.clientX, y: e.clientY };
  if (row && !tree.sel.has(keyOf(row))) { selectOnly(keyOf(row)); paintSelection(); }
  const items: Selection = row ? selected() : { notes: [], pdfs: [], folders: [] };
  const { notes, pdfs, folders } = items;
  const trash = { label: 'Move to Trash', icon: 'trash', danger: true, shortcut: '⌫', run: deleteSelection };
  if (row && notes.length + pdfs.length + folders.length > 1) {
    openMenu([
      { section: describe(items) },
      { label: 'Move to folder…', icon: 'folder', run: () => pickFolder(at, items) },
      notes.length > 0 && { label: notes.length > 1 ? 'Copy links' : 'Copy link', icon: 'link', run: () => copyLinks(notes) },
      'sep',
      trash,
    ], at);
    return;
  }
  if (row && pdfs.length === 1 && !notes.length && !folders.length) {
    const p = pdfs[0];
    const el = treeEl().querySelector<HTMLElement>(`.nt-row.pdf[data-path="${CSS.escape(p)}"]`)!;
    openMenu([
      { label: 'Open', icon: 'open', run: () => openPdfRow(el), shortcut: '↵' },
      { label: 'Open in New Tab', icon: 'pdf', run: () => openPdfRow(el, true) },
      { label: 'Rename…', icon: 'edit', run: () => renamePdf(p), shortcut: 'F2' },
      { label: 'Move to folder…', icon: 'folder', run: () => pickFolder(at, items) },
      'sep',
      trash,
    ], at);
    return;
  }
  if (row && notes.length === 1) {
    const p = notes[0];
    openMenu([
      { label: 'Open', icon: 'open', run: () => hooks.openNote(p), shortcut: '↵' },
      { label: 'Open in New Tab', icon: 'doc', run: () => hooks.openNote(p, { keep: true }) },
      { label: 'Open in New Window', icon: 'newWindow', run: () => openNewWindow({ note: p }) },
      { label: 'Rename…', icon: 'edit', run: () => renameNote(p), shortcut: 'F2' },
      { label: 'Move to folder…', icon: 'folder', run: () => pickFolder(at, items) },
      { label: 'Copy link', icon: 'link', run: () => copyLinks([p]) },
      noteHooks.graph && { label: `Show in ${noteHooks.graph.title || 'Graph'}`, icon: 'link', run: () => hooks.openNotesGraph(`note:${p}`) },
      { label: 'Export…', icon: 'export', run: () => exportMenu(p, at) },
      'sep',
      trash,
    ], at);
    return;
  }
  const fp = folders[0] || '';
  const own = fp ? ws.overview.folder_colors?.[fp] || null : null;
  const color = (c: string | null) => async () => {
    try { ws.overview.folder_colors = (await api.setNoteFolderColor(fp, c)).folder_colors; } catch (e) { toastError(e); }
    await refreshTree();
  };
  const folderEntries: MenuEntry[] = fp ? [
    'sep',
    { label: 'Rename…', icon: 'edit', run: () => renameFolder(fp), shortcut: 'F2' },
    { label: 'Move to folder…', icon: 'folder', run: () => pickFolder(at, items) },
    { section: 'Colour' },
    ...PALETTE.map((c) => ({ label: colorName(c), swatch: c, checked: own === c, run: color(c) })),
    { label: 'No Colour', swatch: '', checked: !own, run: color(null) },
    'sep',
    trash,
  ] : [];
  openMenu([
    { label: 'New note here', icon: 'plus', run: () => newNote(fp) },
    { label: 'New folder here', icon: 'folderPlus', run: () => newFolder(fp) },
    { label: 'Add PDF…', icon: 'pdf', run: () => pickPdfs(fp) },
    ...folderEntries,
  ], at);
}

function bindDnD(el: HTMLElement): void {
  el.addEventListener('dragstart', (e) => {
    const row = (e.target as Element).closest<HTMLElement>('.nt-row');
    if (!row || !e.dataTransfer) { e.preventDefault(); return; }
    const key = keyOf(row);
    // drag the whole selection when the row is part of it, otherwise just this row
    const items = tree.sel.has(key) ? selected()
      : { notes: key[0] === 'n' ? [key.slice(2)] : [], pdfs: key[0] === 'p' ? [key.slice(2)] : [], folders: key[0] === 'f' ? [key.slice(2)] : [] };
    e.dataTransfer.setData(NOTE_MIME, JSON.stringify(items));
    const links = [...items.notes.map((p) => `[[${stemOf(p)}]]`), ...(items.pdfs || []).map((p) => `[[${p}]]`)];
    if (links.length) e.dataTransfer.setData('text/plain', links.join('\n'));  // dropping into the editor links them
    e.dataTransfer.effectAllowed = 'copyMove';
  });
  const target = (e: Event) => (e.target as Element).closest<HTMLElement>('[data-drop-folder]');
  const hasFiles = (e: DragEvent) => [...(e.dataTransfer?.types || [])].includes('Files');
  el.addEventListener('dragover', (e) => {
    if (!hasFiles(e) && ![...(e.dataTransfer?.types || [])].includes(NOTE_MIME)) return;
    const t = target(e);
    if (!t) return;
    e.preventDefault();
    el.querySelectorAll('.drop-target').forEach((x) => x.classList.remove('drop-target'));
    if (t !== el) t.classList.add('drop-target');
  });
  el.addEventListener('dragleave', (e) => {
    const t = e.target as Element;
    if (t.classList?.contains('drop-target')) t.classList.remove('drop-target');
  });
  el.addEventListener('drop', (e) => {
    el.querySelectorAll('.drop-target').forEach((x) => x.classList.remove('drop-target'));
    const t = target(e);
    if (hasFiles(e) && t) {   // files from the computer: PDFs go into the folder under the pointer
      e.preventDefault();
      // a note or PDF row is not a folder: its files go next to it
      const row = (e.target as Element).closest<HTMLElement>('.nt-row.note, .nt-row.pdf');
      void addPdfs([...(e.dataTransfer?.files || [])], row?.dataset.path ? parentOf(row.dataset.path) : t.dataset.dropFolder!);
      return;
    }
    let items: Partial<Selection> | null;
    try { items = JSON.parse(e.dataTransfer?.getData(NOTE_MIME) || 'null'); } catch { items = null; }
    if (!items || !t) return;
    e.preventDefault();
    moveItems({ notes: items.notes || [], pdfs: items.pdfs || [], folders: items.folders || [] }, t.dataset.dropFolder!);
  });
}

export function initTreeInput(): void {
  const el = treeEl();
  el.addEventListener('click', onClick);
  el.addEventListener('dblclick', onDblClick);
  el.addEventListener('keydown', onKey);
  el.addEventListener('contextmenu', onContextMenu);
  bindDnD(el);
}
