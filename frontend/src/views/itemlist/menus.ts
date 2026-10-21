// The item list's context menus (items and attachments) and the actions they share with the item
// pane: renaming and deleting attachments, copying citation keys, picking files.
import * as A from '../../actions.js';
import { itemActions } from '../../extend.js';
import { type ApiError, api, openAttachment, openPdfInNewWindow } from '../../api.js';
import { openNewWindow } from '../../windows.js';
import { currentFolderId, emit, folderById, selectedItems, state } from '../../store.js';
import { promptDialog } from '../../ui/dialog.js';
import { type MenuEntry, openMenu } from '../../ui/menu.js';
import { toast, toastError } from '../../ui/toast.js';
import type { Attachment, Item } from '../../types.js';
import { copyText, modLabel, plural } from '../../util.js';
import { openExport } from '../exportDialog.js';
import { openMetadataReview } from '../metadataReview.js';
import { fetchReferencesBulk } from '../referencesSection.js';
import { addSelectionToFolder, editTagsDialog, moveSelectionToFolder } from '../organizeDialogs.js';

type At = { x: number; y: number } | Element;

/** Open the item's paper note, or else the first note that cites it. */
export async function openItemNote(it: Item | undefined): Promise<void> {
  if (!it) return;
  const n = state.noteKeys[it.citation_key || ''];
  if (n?.paper) { emit('open-note', { path: n.paper }); return; }
  try {
    const res = await api.itemNotes(it.id);
    const first = res.paper_notes[0] || res.citing[0];
    // a paper note opens at the top; a citing note at the line that cites the item
    if (first) emit('open-note', { path: first.path, line: (res.paper_notes[0] ? null : res.citing[0]?.line) || null });
  } catch (e) { toastError(e); }
}

async function newPaperNote(it: Item): Promise<void> {
  try {
    const res = await api.createNote({ item_id: it.id });
    emit('open-note', { path: res.path });
  } catch (e) {
    const existing = ((e as ApiError).data as { path?: string } | undefined)?.path;   // the item has a paper note already
    if (existing) emit('open-note', { path: existing }); else toastError(e);
  }
}

/** The menu for the selected items. */
export function itemMenu(at: At): void {
  const items = selectedItems();
  if (!items.length) return;
  const one = items.length === 1 ? items[0] : null;
  const ids = items.map((i) => i.id);
  if (state.view === 'trash') {
    openMenu([
      { label: 'Restore to Library', icon: 'restore', run: A.restoreSelected },
      'sep',
      { label: `Delete Permanently…`, icon: 'trash', danger: true, run: () => A.deleteForever(ids) },
    ], at);
    return;
  }
  const atts = one?.attachments || [];
  const hasPdf = atts.length > 0;
  const onlineUrl = one && (one.url || (one.doi ? `https://doi.org/${one.doi}` : ''));
  const notesOn = state.settings.notes_enabled !== false;
  const noteInfo = one && state.noteKeys[one.citation_key || ''];
  const folder = currentFolderId();
  openMenu([
    one && state.view === 'duplicates' && { label: 'Keep This One, Trash the Others', icon: 'check', run: () => A.keepOnly(one.id) },
    one && state.view === 'duplicates' && 'sep',
    ...(one && atts.length > 1
      ? atts.map((a) => ({ label: `Open “${a.name || 'PDF'}”`, icon: 'pdf', run: () => openAttachment(one, a) }))
      : [one && { label: hasPdf ? 'Open PDF' : 'Open PDF (none attached)', icon: 'pdf', disabled: !hasPdf, run: () => A.openPdf(one), shortcut: '↵' }]),
    one && atts.length === 1 && { label: 'Open PDF in New Window', icon: 'newWindow', run: () => openPdfInNewWindow(one, atts[0]) },
    one && { label: 'Show in New Window', icon: 'newWindow', run: () => openNewWindow({ item: one.id, view: state.view.startsWith('folder:') ? state.view : undefined }) },
    one && { label: 'View Online', icon: 'globe', disabled: !onlineUrl, run: () => window.open(onlineUrl || '', '_blank') },
    one && notesOn && (noteInfo ? { label: noteInfo.paper ? 'Open Paper Note' : 'Open Notes Citing This', icon: 'edit', run: () => openItemNote(one) }
      : { label: 'New Paper Note', icon: 'edit', disabled: !one.citation_key, run: () => newPaperNote(one) }),
    one && 'sep',
    { label: one ? 'Retrieve Metadata…' : `Retrieve Metadata for ${items.length} Items…`, icon: 'sparkle', run: () => openMetadataReview(ids) },
    { label: one ? 'Find Available PDF' : `Find PDFs for ${items.length} Items`, icon: 'find', run: () => (one ? A.findPdf(one.id) : A.findPdfs(ids)) },
    { label: one ? 'Fetch References' : `Fetch References for ${items.length} Items`, icon: 'link', run: () => fetchReferencesBulk(ids) },
    one && notesOn && { label: 'Show in Graph', icon: 'link', run: () => emit('show-graph', `item:${one.id}`) },
    one && { label: 'Add PDF Attachment…', icon: 'paperclip', run: () => pickFiles((files) => A.uploadAttachments(one.id, files)) },
    'sep',
    { label: one ? 'Add to Folder…' : `Add ${items.length} Items to Folder…`, icon: 'folderPlus', run: () => addSelectionToFolder(ids) },
    !!folder && { label: 'Move to Another Folder…', icon: 'folder', run: () => moveSelectionToFolder(ids) },
    !!folder && { label: `Remove from “${folderById(folder)?.name || 'Folder'}”`, icon: 'unfiled', run: () => A.removeFromFolder(ids, folder), shortcut: '⌫' },
    { label: one ? 'Edit Tags…' : `Tags for ${items.length} Items…`, icon: 'tag', run: () => editTagsDialog(ids), shortcut: `${modLabel}T` },
    'sep',
    { label: one ? 'Copy Citation Key' : 'Copy Citation Keys', icon: 'key', run: () => copyKeys(items), shortcut: `${modLabel}⇧C` },
    { label: 'Copy as BibTeX', icon: 'copy', run: () => copyBibtex(ids), shortcut: `${modLabel}C` },
    { label: one ? 'Export Item…' : `Export ${items.length} Items…`, icon: 'export', run: () => openExport(ids) },
    { label: one ? 'Regenerate Citation Key' : 'Regenerate Citation Keys', icon: 'refresh', run: () => A.regenerateCitekeys(ids) },
    ...pluginActions(items),
    'sep',
    { label: one ? 'Move Item to Trash' : `Move ${items.length} Items to Trash`, icon: 'trash', danger: true, run: A.trashSelected, shortcut: folder ? `${modLabel}⌫` : '⌫' },
  ], at);
}

/** What plugins offer for these items (extend.ts), after a separator. */
function pluginActions(items: Item[]): MenuEntry[] {
  const usable = itemActions.all().filter((a) => { try { return !a.when || a.when(items); } catch { return false; } });
  return usable.length ? ['sep', ...usable.map((a) => ({ label: a.title, icon: a.icon || 'plugin', run: () => a.run(items) }))] : [];
}

/** The menu for an attachment row. */
export function attachmentMenu(it: Item | undefined, attId: string | undefined, at: At): void {
  const att = it?.attachments?.find((a) => String(a.id) === String(attId));
  if (!it || !att) return;
  openMenu([
    { label: 'Open', icon: 'open', run: () => openAttachment(it, att) },
    { label: 'Open in New Window', icon: 'newWindow', run: () => openPdfInNewWindow(it, att) },
    { label: 'Rename…', icon: 'edit', run: () => renameAttachment(att) },
    { label: 'Download', icon: 'download', run: () => { const a = document.createElement('a'); a.href = `/pdfs/${encodeURIComponent(att.url)}`; a.download = att.name || 'file.pdf'; a.click(); } },
    'sep',
    { label: 'Delete Attachment', icon: 'trash', danger: true, run: () => deleteAttachment(att) },
  ], at);
}

export async function renameAttachment(att: Attachment): Promise<void> {
  const name = await promptDialog('Name', { title: 'Rename Attachment', value: att.name || '', confirm: 'Rename' });
  if (!name) return;
  try { await api.renameAttachment(att.id, name); await A.refreshItems([att.paper_id]); } catch (e) { toastError(e); }
}

export async function deleteAttachment(att: Attachment): Promise<void> {
  const { confirmDialog } = await import('../../ui/dialog.js');
  if (!(await confirmDialog(`Delete “${att.name}”?`, { title: 'Delete attachment', confirm: 'Delete', danger: true, detail: 'The file is removed from disk.' }))) return;
  try { await api.deleteAttachment(att.id); await A.refreshItems([att.paper_id]); toast('Attachment deleted'); } catch (e) { toastError(e); }
}

export async function copyKeys(items: Item[]): Promise<void> {
  const keys = items.map((i) => i.citation_key).filter(Boolean);
  if (!keys.length) { toast('No citation keys yet — use “Regenerate Citation Key”.', { type: 'error' }); return; }
  await copyText(keys.join(', '));
  toast(`Copied ${keys.length === 1 ? keys[0] : plural(keys.length, 'key')}`, { type: 'success' });
}

async function copyBibtex(ids: string[]): Promise<void> {
  try {
    const res = await api.exportText('bibtex', ids);
    await copyText(res.text);
    toast(`Copied BibTeX for ${plural(res.count, 'item')}`, { type: 'success' });
  } catch (e) { toastError(e); }
}

/** Ask for files (PDFs unless ``accept`` says otherwise) and pass them to ``cb``. */
export function pickFiles(cb: (files: FileList) => void, accept = 'application/pdf,.pdf', multiple = true): void {
  const input = document.createElement('input');
  input.type = 'file'; input.accept = accept; input.multiple = multiple;
  input.onchange = () => { if (input.files?.length) cb(input.files); };
  input.click();
}
