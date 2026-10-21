// The notes list on the left: folders and notes as a tree, which folders are folded, the filter
// (names, then notes whose text matches) and the selection, as row keys: 'n:<path>' for notes,
// 'f:<path>' for folders.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { emit } from '../../store.js';
import { toastError } from '../../ui/toast.js';
import type { NoteInfo } from '../../types.js';
import { escapeHtml, storage, store } from '../../util.js';
import { folderColor, nameOf, notesOpen, parentOf, stemOf, within, ws } from './workspace.js';

/** A folder icon's colour: its own (full), or the one it has from a folder above (lighter). */
function colorStyle(path: string): string {
  const own = ws.overview.folder_colors?.[path];
  const c = own || folderColor(path);
  return c ? ` style="color:${escapeHtml(c)}${own ? '' : ';opacity:.55'}"` : '';
}

interface FolderNode { path: string; name: string; folders: FolderNode[]; notes: NoteInfo[] }

export const tree = {
  sel: new Set<string>(),
  anchor: null as string | null,     // where a shift-click / shift-arrow range starts
  cursor: null as string | null,     // the row the keyboard is on
  collapsed: new Set<string>(storage<string[]>('zc.notes.collapsed', [])),
  filter: '',
};
let hitTimer: ReturnType<typeof setTimeout> | undefined;

export const nKey = (p: string) => `n:${p}`;
export const fKey = (p: string) => `f:${p}`;
export const keyOf = (row: HTMLElement) => (row.classList.contains('folder') ? fKey(row.dataset.folder!) : nKey(row.dataset.path!));
export const treeEl = () => ws.root.querySelector<HTMLElement>('.notes-tree')!;
export const visibleKeys = () => [...treeEl().querySelectorAll<HTMLElement>('.nt-row')].map(keyOf);

/** Load the notes listing again (and redraw the tree if Notes is open). */
export async function refreshTree(): Promise<void> {
  try {
    ws.overview = await api.notes();
  } catch (e) { if (notesOpen()) toastError(e); return; }
  emit('notes-count', ws.overview.notes.length);
  if (notesOpen()) paintTree();
}

export function selectOnly(key: string | null): void { tree.sel = new Set(key ? [key] : []); tree.anchor = tree.cursor = key; }
export function toggleSelected(key: string): void {
  if (tree.sel.has(key)) tree.sel.delete(key); else tree.sel.add(key);
  tree.anchor = tree.cursor = key;
}
export function selectRange(key: string): void {
  const keys = visibleKeys();
  const b = keys.indexOf(key);
  const a = tree.anchor && keys.includes(tree.anchor) ? keys.indexOf(tree.anchor) : b;
  tree.sel = new Set(keys.slice(Math.min(a, b), Math.max(a, b) + 1));
  tree.cursor = key;
}
/** The selected notes and folders (a note inside a selected folder goes with the folder). */
export function selected(): { notes: string[]; folders: string[] } {
  const folders = [...tree.sel].filter((k) => k[0] === 'f').map((k) => k.slice(2));
  const notes = [...tree.sel].filter((k) => k[0] === 'n').map((k) => k.slice(2)).filter((p) => !folders.some((f) => within(p, f)));
  return { notes, folders: folders.filter((f) => !folders.some((o) => within(f, o))) };
}

export function saveCollapsed(): void { store('zc.notes.collapsed', [...tree.collapsed]); }

export function toggleFolder(path: string, open = tree.collapsed.has(path)): void {
  if (open) tree.collapsed.delete(path); else tree.collapsed.add(path);
  saveCollapsed();
  paintTree();
}

function buildTree(): FolderNode {
  const folders = new Map<string, FolderNode>([['', { path: '', name: '', folders: [], notes: [] }]]);
  const ensure = (path: string): FolderNode => {
    const known = folders.get(path);
    if (known) return known;
    const node: FolderNode = { path, name: nameOf(path), folders: [], notes: [] };
    folders.set(path, node);
    ensure(parentOf(path)).folders.push(node);
    return node;
  };
  ws.overview.folders.forEach(ensure);
  for (const n of ws.overview.notes) ensure(parentOf(n.path)).notes.push(n);
  const cmp = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: 'base', numeric: true });
  for (const f of folders.values()) {
    f.folders.sort((a, b) => cmp(a.name, b.name));
    f.notes.sort((a, b) => cmp(stemOf(a.path), stemOf(b.path)));
  }
  return folders.get('')!;
}

export function paintTree(): void {
  const el = treeEl();
  const active = ws.session?.path;
  const { sel, collapsed, filter } = tree;
  const matches = (n: NoteInfo) => !filter || stemOf(n.path).toLowerCase().includes(filter) || (n.title || '').toLowerCase().includes(filter);
  const cls = (key: string) => `${sel.has(key) ? ' selected' : ''}${tree.cursor === key ? ' cursor' : ''}`;
  const rows: (string | null)[] = [];
  const walk = (node: FolderNode, depth: number) => {
    for (const f of node.folders) {
      const open = filter || !collapsed.has(f.path);
      const before = rows.length;
      rows.push(null); // placeholder for the folder row
      if (open) walk(f, depth + 1);
      const hasHits = rows.length > before + 1;
      if (filter && !hasHits) { rows.length = before; continue; }
      const k = fKey(f.path);
      rows[before] = `<div class="nt-row folder${cls(k)}" role="treeitem" aria-expanded="${!!open}" aria-selected="${sel.has(k)}" data-folder="${escapeHtml(f.path)}" data-drop-folder="${escapeHtml(f.path)}" draggable="true" style="--d:${depth}">
          <span class="twisty ${open ? 'open' : ''}">${icon('chevron', 'icon-sm')}</span><span class="nt-folder-icon"${colorStyle(f.path)}>${icon('folder')}</span><span class="nm">${escapeHtml(f.name)}</span></div>`;
    }
    for (const n of node.notes) {
      if (!matches(n)) continue;
      const k = nKey(n.path);
      rows.push(`<div class="nt-row note${n.path === active ? ' active' : ''}${n.type === 'paper' ? ' paper' : ''}${cls(k)}" role="treeitem" aria-selected="${sel.has(k)}" data-path="${escapeHtml(n.path)}" draggable="true"
          title="${escapeHtml(n.title && n.title !== stemOf(n.path) ? `${n.title}\n${n.path}` : n.path)}" style="--d:${depth}">
          <span class="twisty none"></span>${icon(n.type === 'paper' ? 'book' : 'doc')}<span class="nm">${escapeHtml(stemOf(n.path))}</span></div>`);
    }
  };
  walk(buildTree(), 0);
  el.innerHTML = rows.join('') || (filter ? '' : '<div class="side-empty">No notes yet. Create one with <b>+</b>.</div>');
  // Only what can be seen stays selected, so Delete never takes hidden notes with it.
  const shown = new Set(visibleKeys());
  tree.sel = new Set([...tree.sel].filter((k) => shown.has(k)));
  if (tree.cursor && !shown.has(tree.cursor)) tree.cursor = null;
  if (filter.length >= 2) { clearTimeout(hitTimer); hitTimer = setTimeout(paintHits, 150); }
}

/** Show a selection change on the rows already drawn (no redraw, so double-clicks still land). */
export function paintSelection(): void {
  for (const row of treeEl().querySelectorAll<HTMLElement>('.nt-row')) {
    const k = keyOf(row);
    row.classList.toggle('selected', tree.sel.has(k));
    row.classList.toggle('cursor', tree.cursor === k);
    row.setAttribute('aria-selected', String(tree.sel.has(k)));
  }
}

/** Filter the tree by ``text`` (names at once, text matches a moment later). */
export function setFilter(text: string): void {
  tree.filter = text;
  paintTree();
  clearTimeout(hitTimer);
  hitTimer = setTimeout(paintHits, 250);
}

/** Notes whose text contains the filter words (below the name matches in the tree). */
async function paintHits(): Promise<void> {
  const el = treeEl();
  el.querySelector('.notes-hits')?.remove();
  const q = tree.filter;
  if (q.length < 2) return;
  let res;
  try { res = (await api.searchNotes(q)).results; } catch { return; }
  if (q !== tree.filter) return;
  const nameHits = new Set([...el.querySelectorAll<HTMLElement>('.nt-row.note')].map((r) => r.dataset.path));
  const rows = res.filter((r) => !nameHits.has(r.path));
  if (!rows.length) return;
  el.querySelector('.notes-hits')?.remove();
  el.insertAdjacentHTML('beforeend', `<div class="notes-hits"><div class="ns-title">Text matches</div>${rows.map((r) => `
    <button class="nt-hit" data-path="${escapeHtml(r.path)}"><div class="nm">${escapeHtml(r.title || stemOf(r.path))}</div>
      <div class="snip">${r.snippet}</div></button>`).join('')}</div>`);
}
