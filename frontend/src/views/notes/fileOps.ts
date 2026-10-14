// Changing the notes folder from the tree: new notes and folders, renaming and moving (the open
// note follows), the notes trash (with Undo) and rescanning the folder.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { state } from '../../store.js';
import { confirmDialog, openDialog, promptDialog } from '../../ui/dialog.js';
import { openMenu } from '../../ui/menu.js';
import { toast, toastError } from '../../ui/toast.js';
import type { NoteMoved } from '../../types.js';
import { escapeHtml, formatDate, h, plural } from '../../util.js';
import { RETENTION_LABELS } from './attachmentsDialog.js';
import type { NoteSession } from './noteSession.js';
import {
  fKey, nKey, refreshTree, saveCollapsed, selectOnly, selected, tree, treeEl, visibleKeys,
} from './tree.js';
import { hooks, nameOf, parentOf, stemOf, within, ws } from './workspace.js';

/** Notes and folders to act on together. */
export interface Selection { notes: string[]; folders: string[] }

/** The folder a new note goes in: the folder under the keyboard, else the open note's. */
export function folderOfSelection(): string {
  const f = tree.cursor?.startsWith('f:') ? tree.cursor.slice(2) : null;
  return f ?? (ws.session ? parentOf(ws.session.path) : '');
}

export async function newNote(folder = folderOfSelection()): Promise<void> {
  const title = await promptDialog('Title', { title: 'New note', placeholder: 'Untitled', confirm: 'Create',
    hint: folder ? `In ${folder}/` : 'At the top level of your notes' });
  if (title === null) return;
  try {
    const res = await api.createNote({ title: title || 'Untitled', folder });
    if (folder) tree.collapsed.delete(folder);
    await refreshTree();
    await hooks.openNote(res.path);
  } catch (e) { toastError(e); }
}

export async function newFolder(parent = ''): Promise<void> {
  const name = await promptDialog('Folder name', { title: 'New folder', confirm: 'Create', hint: parent ? `Inside ${parent}/` : '' });
  if (!name) return;
  try {
    const res = await api.newNoteFolder(parent ? `${parent}/${name}` : name);
    if (parent) tree.collapsed.delete(parent);
    saveCollapsed();
    selectOnly(fKey(res.path));
    refreshTree();
  } catch (e) { toastError(e); }
}

function reportBroken(broken: NoteMoved['broken_links']): void {
  if (!broken?.length) return;
  const names = [...new Set(broken.map((b) => stemOf(b.path)))];
  toast(`${plural(broken.length, 'link')} to the old name in ${names.slice(0, 3).join(', ')}${names.length > 3 ? '…' : ''} no longer resolve${broken.length === 1 ? 's' : ''}.`,
    { timeout: 9000 });
}

async function moveNote(path: string, to: string, { quiet = false } = {}): Promise<void> {
  if (!to || to === path) return;
  try {
    const session = ws.session;
    const isOpen = session?.path === path;
    if (session && isOpen) { await session.save(); if (session.dirty) throw new Error('Save the note before moving it.'); }
    const res = await api.moveNote(path, to, isOpen ? session!.saved.hash : undefined);
    if (isOpen) await reopenAs(res.path);
    if (tree.sel.delete(nKey(path))) tree.sel.add(nKey(res.path));
    if (tree.cursor === nKey(path)) tree.anchor = tree.cursor = nKey(res.path);
    if (!quiet) await refreshTree();
    reportBroken(res.broken_links);
  } catch (e) { toastError(e); }
}

/** After a dialog, give the keyboard back to the file list if that is where it was. */
function keepTreeFocus(): () => void {
  const was = treeEl().contains(document.activeElement);
  return () => { if (was) treeEl().focus({ preventScroll: true }); };
}

/** The open note was moved: keep editing it under its new path, with anything typed meanwhile. */
async function reopenAs(path: string): Promise<void> {
  const old = ws.session!;
  const typed = old.text;
  old.dispose();
  ws.session = null;
  await hooks.doOpen(path);
  const reopened = ws.session as NoteSession | null;   // set by doOpen
  if (reopened && typed !== reopened.text) ws.editor!.replace(typed);   // saved by the new session
}

export async function renameNote(path: string): Promise<void> {
  const stem = stemOf(path);
  const back = keepTreeFocus();
  const name = await promptDialog('Name', { title: 'Rename note', value: stem, confirm: 'Rename' });
  back();
  if (!name || name === stem) return;
  const folder = parentOf(path);
  const clean = name.replace(/\.md$/i, '').replace(/\//g, '-');
  return moveNote(path, `${folder ? `${folder}/` : ''}${clean}.md`);
}

/** Rename or move a folder with everything in it (the open note follows). */
async function moveFolder(path: string, to: string, { quiet = false } = {}): Promise<void> {
  if (!to || to === path) return;
  try {
    const session = ws.session;
    const open = session?.path;
    const inside = !!open && within(open, path);
    if (session && inside) { await session.save(); if (session.dirty) throw new Error('Save the open note before moving its folder.'); }
    const res = await api.moveNoteFolder(path, to);
    const moved = (p: string) => (p === path || within(p, path) ? res.path + p.slice(path.length) : p);
    tree.collapsed = new Set([...tree.collapsed].map(moved));
    saveCollapsed();
    const keep = { sel: [...tree.sel], cursor: tree.cursor };   // reopening the note would move the selection to it
    if (open && inside) await reopenAs(moved(open));
    tree.sel = new Set(keep.sel.map((k) => `${k[0]}:${moved(k.slice(2))}`));
    tree.anchor = tree.cursor = keep.cursor ? `${keep.cursor[0]}:${moved(keep.cursor.slice(2))}` : null;
    if (!quiet) await refreshTree();
    reportBroken(res.broken_links);
  } catch (e) { toastError(e); }
}

export async function renameFolder(path: string): Promise<void> {
  const name = nameOf(path);
  const back = keepTreeFocus();
  const nu = await promptDialog('Name', { title: 'Rename folder', value: name, confirm: 'Rename' });
  back();
  if (!nu || nu.trim() === name) return;
  const parent = parentOf(path);
  return moveFolder(path, `${parent ? `${parent}/` : ''}${nu.trim().replace(/\//g, '-')}`);
}

/** Move notes and folders into ``dest`` ('' = top level); things already there are skipped. */
export async function moveItems({ notes, folders }: Selection, dest: string): Promise<void> {
  const into = (p: string) => `${dest ? `${dest}/` : ''}${nameOf(p)}`;
  for (const f of folders) {
    if (f === dest || within(dest, f) || parentOf(f) === dest) continue;
    await moveFolder(f, into(f), { quiet: true });
  }
  for (const n of notes) {
    if (parentOf(n) !== dest) await moveNote(n, into(n), { quiet: true });
  }
  if (dest) { tree.collapsed.delete(dest); saveCollapsed(); }
  await refreshTree();
}

/** A menu of folders to move things into. */
export function pickFolder(at: { x: number; y: number }, items: Selection): void {
  const bad = (f: string) => items.folders.some((x) => f === x || within(f, x));
  const m = openMenu([
    { section: 'Move to' },
    { label: 'Top level', icon: 'folder', run: () => moveItems(items, '') },
    ...ws.overview.folders.filter((f) => !bad(f)).map((f) => ({ label: f, icon: 'folder', run: () => moveItems(items, f) })),
  ], at);
  m.style.maxHeight = '60vh';
  m.style.overflow = 'auto';
}

/** "folder “x” and 3 notes": for menus and confirmations. */
export function describe({ notes, folders }: Selection): string {
  const parts = [];
  if (folders.length === 1) parts.push(`folder “${nameOf(folders[0])}”`);
  else if (folders.length) parts.push(`${folders.length} folders`);
  if (notes.length === 1) parts.push(`“${stemOf(notes[0])}”`);
  else if (notes.length) parts.push(plural(notes.length, 'note'));
  return parts.join(' and ');
}

export function copyLinks(notes: string[]): void {
  const text = notes.map((p) => `[[${stemOf(p)}]]`).join('\n');
  navigator.clipboard?.writeText(text);
  toast(notes.length === 1 ? `Copied ${text}` : `Copied ${plural(notes.length, 'link')}`);
}

/** Move the selected notes and folders to the notes trash (one Undo brings them all back). */
export async function deleteSelection(): Promise<void> {
  const items = selected();
  const { notes, folders } = items;
  if (!notes.length && !folders.length) return;
  const inFolders = ws.overview.notes.filter((n) => folders.some((f) => within(n.path, f))).length;
  const what = describe(items);
  if ((state.settings.confirm_trash || inFolders) && !(await confirmDialog(
    `Move ${what}${inFolders ? ` (with ${plural(inFolders, 'note')} inside)` : ''} to the notes trash?`,
    { title: 'Delete', confirm: 'Move to Trash', danger: true, detail: 'You can undo this, or restore them later from “Deleted notes…”.' }))) return;
  // the row to land on afterwards: the next one that stays
  const gone = (k: string) => (k[0] === 'n' && notes.includes(k.slice(2))) || folders.some((f) => k.slice(2) === f || within(k.slice(2), f));
  const keys = visibleKeys();
  const last = Math.max(...keys.map((k, i) => (gone(k) ? i : -1)));
  const next = keys.slice(last + 1).find((k) => !gone(k)) || keys.slice(0, last).reverse().find((k) => !gone(k)) || null;
  try {
    const open = ws.session?.path;
    const closing = !!open && (notes.includes(open) || folders.some((f) => within(open, f)));
    if (closing) { await ws.session!.save(); await hooks.closeCurrent(); }
    const res = await api.trashNotes(notes, folders);
    selectOnly(next);
    await refreshTree();
    treeEl().focus({ preventScroll: true });
    toast(`Moved ${what} to the notes trash.`, {
      timeout: 9000,
      action: { label: 'Undo', run: async () => {
        try {
          const r = await api.restoreNoteBatch(res.trash_id);
          tree.sel = new Set([...r.paths.filter((x) => !folders.some((f) => within(x, f))).map(nKey), ...folders.map(fKey)]);
          tree.anchor = tree.cursor = [...tree.sel][0] || null;
          await refreshTree();
          if (open && closing && r.paths.includes(open)) hooks.openNote(open);
          if (r.skipped.length) toast(`${plural(r.skipped.length, 'file')} stayed in the trash: something new has the same name.`, { timeout: 9000 });
        } catch (e) { toastError(e); }
      } },
    });
  } catch (e) { toastError(e); }
}

export async function showTrash(): Promise<void> {
  let list, retention;
  try { ({ trash: list, retention } = await api.noteTrash()); } catch (e) { toastError(e); return; }
  const trashed = list;
  await openDialog({
    title: 'Deleted notes',
    subtitle: `Deleted notes are kept in the .trash folder inside your notes folder${retention && retention !== 'never'
      ? ` and deleted for good ${RETENTION_LABELS[retention]} (Settings → General).` : '.'}`,
    wide: true,
    render: (b) => {
      b.innerHTML = trashed.length ? `<div class="trash-list">${trashed.map((t, i) => `<div class="trash-row"><span class="mono">${escapeHtml(t.path)}</span>
          <span class="muted" title="${t.deletes_at ? `Deleted for good on ${escapeHtml(formatDate(t.deletes_at))}` : ''}">${escapeHtml(formatDate(t.trashed_at || t.trash_id.slice(0, 8).replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3')))}</span>
          <button class="btn btn-sm" data-restore="${i}">${icon('restore', 'icon-sm')} Restore</button></div>`).join('')}</div>`
        : '<p class="muted" style="margin:0">The notes trash is empty.</p>';
      b.addEventListener('click', async (e) => {
        const btn = (e.target as Element).closest<HTMLElement>('[data-restore]');
        if (!btn) return;
        const t = trashed[Number(btn.dataset.restore)];
        try {
          await api.restoreNote(t.trash_id, t.path);
          btn.closest('.trash-row')!.classList.add('done');
          btn.replaceWith(h('<span class="muted">Restored</span>'));
          refreshTree();
        } catch (err) { toastError(err); }
      });
    },
    buttons: [{ label: 'Close', kind: 'primary' }],
  });
}

export async function rescan(): Promise<void> {
  try {
    const r = await api.rescanNotes();
    await refreshTree();
    toast(`Notes folder rescanned: ${r.added} added, ${r.changed} changed, ${r.removed} removed.`);
    ws.session?.checkRemote();
  } catch (e) { toastError(e); }
}
