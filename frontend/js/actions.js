// User-level operations. Views call these; these talk to the API, update the
// store and emit events. Keeping them here keeps views free of API details.
import { api, openAttachment } from './api.js';
import { byId, currentFolderId, emit, folderById, folderDescendants, selectedItems, state } from './store.js';
import { confirmDialog } from './ui/dialog.js';
import { progressToast, toast, toastError } from './ui/toast.js';
import { plural } from './util.js';

let loadSeq = 0;

export async function loadItems({ keepSelection = true } = {}) {
  const seq = ++loadSeq;
  state.loading = true;
  emit('loading', true);
  try {
    let res;
    try {
      res = await api.listItems({ view: state.view, q: state.query, mode: state.mode, tags: [...state.tagFilter] });
    } catch (e) {
      // the folder / smart folder shown was deleted (e.g. in another tab): fall back to the library
      if (e.status === 404 && state.view !== 'library') { state.view = 'library'; emit('view'); return loadItems({ keepSelection: false }); }
      throw e;
    }
    if (seq !== loadSeq) return;           // a newer request superseded this one
    state.items = res.items;
    state.hits = res.hits || {};
    state.counts = res.counts;
    state.sidebar = res.sidebar || state.sidebar;
    // drop tag filters whose tag no longer exists
    const known = new Set(state.sidebar.tags.map((t) => t.name.toLowerCase()));
    for (const t of [...state.tagFilter]) if (!known.has(t.toLowerCase())) state.tagFilter.delete(t);
    emit('sidebar');
    if (keepSelection) {
      const ids = new Set(state.items.map((i) => i.id));
      state.selection = new Set([...state.selection].filter((id) => ids.has(id)));
    } else state.selection.clear();
    emit('items');
    emit('selection');
  } catch (e) {
    toastError(e);
  } finally {
    if (seq === loadSeq) { state.loading = false; emit('loading', false); }
  }
}

export function setView(view) {
  if (state.view === view) return;
  state.view = view;
  state.selection.clear();
  state.tagFilter.clear();
  emit('view');
  loadItems({ keepSelection: false });
}

export function setSearch(query, mode = state.mode) {
  state.query = query;
  state.mode = mode;
  emit('search');
  loadItems();
}

export function select(ids, { anchor, cursor } = {}) {
  state.selection = new Set(ids);
  if (anchor !== undefined) state.anchor = anchor;
  state.cursor = cursor !== undefined ? cursor : (ids[ids.length - 1] ?? null);
  emit('selection');
}

/** Replace one item in the store after an edit (no full reload). */
export function patchLocal(item) {
  const i = state.items.findIndex((x) => x.id === item.id);
  if (i >= 0) state.items[i] = { ...state.items[i], ...item };
  emit('item-updated', item.id);
}

export async function updateField(id, field, value) {
  try {
    const item = await api.updateItem(id, { [field]: value });
    patchLocal(item);
    return item;
  } catch (e) { toastError(e); throw e; }
}

export async function newItem(data = {}) {
  try {
    if (state.view !== 'library' && !currentFolderId()) setView('library');
    const item = await api.createItem({ item_type: state.settings.default_item_type || 'journalArticle', folder_id: currentFolderId(), ...data });
    await loadItems();
    select([item.id], { anchor: item.id });
    emit('focus-field', { id: item.id, field: 'title' });
    return item;
  } catch (e) { toastError(e); }
}

export async function trashSelected() {
  const items = selectedItems();
  if (!items.length) return;
  const ids = items.map((i) => i.id);
  if (state.view === 'trash') return deleteForever(ids);
  if (state.settings.confirm_trash && !(await confirmDialog(`Move ${plural(ids.length, 'item')} to the Trash?`, { confirm: 'Move to Trash' }))) return;
  try {
    const sorted = [...state.items];
    const next = nextSelectionAfterRemoval(sorted, ids);
    await api.trash(ids);
    await loadItems();
    if (next) select([next], { anchor: next });
    toast(`Moved ${plural(ids.length, 'item')} to the Trash`, {
      action: { label: 'Undo', run: async () => { await api.restore(ids); await loadItems(); select(ids); } },
    });
  } catch (e) { toastError(e); }
}

function nextSelectionAfterRemoval(items, ids) {
  const rows = document.querySelectorAll('.row[data-id]');
  const order = [...rows].map((r) => r.dataset.id);
  const set = new Set(ids);
  const last = Math.max(...ids.map((id) => order.indexOf(id)));
  for (let i = last + 1; i < order.length; i++) if (!set.has(order[i])) return order[i];
  for (let i = last - 1; i >= 0; i--) if (!set.has(order[i])) return order[i];
  return null;
}

/** Duplicates view: keep one item of a group and move the others to the Trash. */
export async function keepOnly(id) {
  const keep = byId(id);
  if (!keep || keep.dup_group === undefined) return;
  const others = state.items.filter((i) => i.dup_group === keep.dup_group && i.id !== id).map((i) => i.id);
  if (!others.length) return;
  try {
    await api.trash(others);
    await loadItems();
    toast(`Kept “${(keep.title || 'item').slice(0, 50)}”, moved ${plural(others.length, 'duplicate')} to the Trash`, {
      type: 'success',
      action: { label: 'Undo', run: async () => { await api.restore(others); await loadItems(); } },
    });
  } catch (e) { toastError(e); }
}

export async function restoreSelected() {
  const ids = [...state.selection];
  if (!ids.length) return;
  await api.restore(ids);
  await loadItems();
  toast(`Restored ${plural(ids.length, 'item')} to My Library`, { type: 'success' });
}

export async function deleteForever(ids) {
  if (!(await confirmDialog(`Permanently delete ${plural(ids.length, 'item')}?`,
    { title: 'Delete permanently', confirm: 'Delete', danger: true, detail: 'Attached files are deleted too. This cannot be undone.' }))) return;
  try {
    await api.deleteForever(ids);
    await loadItems();
    toast(`Deleted ${plural(ids.length, 'item')}`);
  } catch (e) { toastError(e); }
}

export async function emptyTrash() {
  const n = state.counts.trash;
  if (!n) return;
  if (!(await confirmDialog(`Permanently delete all ${plural(n, 'item')} in the Trash?`,
    { title: 'Empty Trash', confirm: 'Empty Trash', danger: true, detail: 'Attached files are deleted too. This cannot be undone.' }))) return;
  await api.emptyTrash();
  await loadItems();
  toast('Trash emptied');
}

export function openPdf(item, att) {
  item = typeof item === 'string' ? byId(item) : item;
  if (!item) return;
  const a = att || (item.attachments || [])[0];
  if (a) openAttachment(item, a);
  else if (item.url) window.open(item.url, '_blank');
  else toast('This item has no PDF yet. Try “Find Available PDF” or add one.', { timeout: 5000 });
}

export async function findPdf(id) {
  const item = byId(id);
  const close = toast(`Searching for a PDF of “${(item?.title || 'item').slice(0, 60)}”…`, { timeout: 0 });
  try {
    const res = await api.findPdf(id);
    close();
    if (res.status === 'success') {
      toast('PDF found and attached', { type: 'success' });
      await loadItems();
      state.expanded.add(id);
      emit('items');
    } else toast(res.message || 'No PDF found', { type: 'error' });
  } catch (e) { close(); toastError(e); }
}

/**
 * Find PDFs for several items, one after another (gentle on Unpaywall/arXiv).
 * Items that already have an attachment are skipped. Cancellable.
 */
export async function findPdfs(ids) {
  const items = ids.map(byId).filter(Boolean);
  const todo = items.filter((i) => !(i.attachments || []).length);
  const skipped = items.length - todo.length;
  if (!todo.length) return toast(`All ${plural(items.length, 'selected item')} already have a PDF`);
  let cancelled = false;
  const pt = progressToast('Finding PDFs…', { onCancel: () => { cancelled = true; } });
  const found = [], missing = [], failed = [];
  for (let n = 0; n < todo.length && !cancelled; n++) {
    const it = todo[n];
    pt.set(`Finding PDFs ${n + 1}/${todo.length}: ${(it.title || 'Untitled').slice(0, 60)}`, n, todo.length);
    try {
      const res = await api.findPdf(it.id);
      (res.status === 'success' ? found : missing).push(it.id);
      if (res.status === 'success') { await loadItems(); }
    } catch (e) {
      failed.push(it.id);
      if (e.status === 0) break;      // server gone: stop instead of failing every item
    }
    if (n < todo.length - 1) await new Promise((r) => setTimeout(r, 400));
  }
  pt.close();
  await loadItems();
  const notFound = [...missing, ...failed];
  const parts = [`Found ${plural(found.length, 'PDF')}`];
  if (notFound.length) parts.push(`${notFound.length} not found`);
  if (skipped) parts.push(`${skipped} already had one`);
  if (cancelled) parts.push('stopped early');
  toast(parts.join(' · '), {
    type: found.length ? 'success' : 'error',
    timeout: 8000,
    action: notFound.length ? { label: 'Select not found', run: () => select(notFound, { anchor: notFound[0] }) } : null,
  });
}

export async function uploadAttachments(id, files) {
  const pdfs = [...files].filter((f) => /pdf$/i.test(f.type) || /\.pdf$/i.test(f.name));
  if (!pdfs.length) return toast('Only PDF files can be attached', { type: 'error' });
  try {
    await api.uploadAttachments(id, pdfs);
    await loadItems();
    state.expanded.add(id);
    emit('items');
    toast(`Attached ${plural(pdfs.length, 'file')}`, { type: 'success' });
  } catch (e) { toastError(e); }
}

export async function addPdfsAsItems(files) {
  const pdfs = [...files].filter((f) => /\.pdf$/i.test(f.name));
  if (!pdfs.length) return;
  const close = toast(`Adding ${plural(pdfs.length, 'PDF')} and looking up metadata…`, { timeout: 0 });
  try {
    const folder = currentFolderId();
    if (!folder && state.view !== 'library') setView('library');
    const res = await api.fromPdfs(pdfs, folder);
    close();
    await loadItems();
    const ids = res.created.map((c) => c.item.id);
    select(ids, { anchor: ids[0] });
    const found = res.created.filter((c) => c.identifier && (c.identifier.doi || c.identifier.arxiv) && !c.note).length;
    toast(found === pdfs.length ? `Added ${plural(pdfs.length, 'item')} with metadata`
      : `Added ${plural(pdfs.length, 'item')} · metadata found for ${found}`, { type: 'success' });
  } catch (e) { close(); toastError(e); }
}

export async function regenerateCitekeys(ids) {
  try {
    const res = await api.regenerateCitekeys(ids);
    await loadItems();
    const keys = Object.values(res.keys);
    toast(keys.length === 1 ? `Citation key: ${keys[0]}` : `Regenerated ${plural(keys.length, 'citation key')}`, { type: 'success' });
  } catch (e) { toastError(e); }
}

/* ============================================================ organisation */
export function toggleTagFilter(name, only = false) {
  const has = state.tagFilter.has(name);
  if (only) state.tagFilter.clear();
  if (has && !only) state.tagFilter.delete(name); else state.tagFilter.add(name);
  emit('tag-filter');
  loadItems();
}
export function clearTagFilter() {
  if (!state.tagFilter.size) return;
  state.tagFilter.clear();
  emit('tag-filter');
  loadItems();
}

export async function createFolder(name, parentId = null, { open = true } = {}) {
  try {
    const f = await api.createFolder(name, parentId);
    if (parentId) { state.openFolders.add(parentId); }
    await loadItems();
    if (open) setView(`folder:${f.id}`);
    return f;
  } catch (e) { toastError(e); return null; }
}

export async function renameFolder(id, name) {
  try { await api.updateFolder(id, { name }); await loadItems(); return true; } catch (e) { toastError(e); return false; }
}

export async function moveFolder(id, parentId) {
  try {
    await api.updateFolder(id, { parent_id: parentId });
    if (parentId) state.openFolders.add(parentId);
    await loadItems();
  } catch (e) { toastError(e); }
}

export async function deleteFolder(id) {
  const f = folderById(id);
  if (!f) return;
  const { confirmDialog } = await import('./ui/dialog.js');
  const subs = state.sidebar.folders.filter((x) => x.parent_id === id).length;
  if (!(await confirmDialog(`Delete the folder “${f.name}”${subs ? ' and its subfolders' : ''}?`, {
    title: 'Delete folder', confirm: 'Delete Folder', danger: true,
    detail: 'The items inside are not deleted — they stay in My Library.',
  }))) return;
  try {
    // leave the view first if it shows this folder or one of its subfolders
    const gone = new Set([id]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const x of state.sidebar.folders) if (gone.has(x.parent_id) && !gone.has(x.id)) { gone.add(x.id); grew = true; }
    }
    const res = await api.deleteFolder(id);
    if (gone.has(currentFolderId())) setView('library');
    await loadItems();
    toast(`Deleted “${f.name}”`);
  } catch (e) { toastError(e); }
}

// An item can be in several folders. Adding links it to a folder (its data is stored
// once); removing unlinks it and never deletes the item.
export async function addToFolder(ids, folderId, { quiet = false } = {}) {
  if (!ids.length || !folderId) return;
  const before = new Set(ids.filter((id) => byId(id)?.folder_ids?.includes(folderId)));
  try {
    await api.changeFolders(ids, [folderId], []);
    await loadItems();
    if (quiet) return;
    const added = ids.filter((id) => !before.has(id));
    const name = folderById(folderId)?.name || 'folder';
    if (!added.length) return toast(`Already in “${name}”`);
    toast(`Added ${plural(added.length, 'item')} to “${name}”`, {
      type: 'success',
      action: { label: 'Undo', run: async () => { await api.changeFolders(added, [], [folderId]); await loadItems(); } },
    });
  } catch (e) { toastError(e); }
}

/**
 * Take items out of a folder. When the folder view also shows subfolder items,
 * they are taken out of those subfolders too, so they disappear from the view.
 */
export async function removeFromFolder(ids, folderId) {
  if (!ids.length || !folderId) return;
  const scope = new Set(state.settings.show_subfolder_items !== false ? folderDescendants(folderId) : [folderId]);
  const removed = ids.map((id) => [id, (byId(id)?.folder_ids || []).filter((f) => scope.has(f))]).filter(([, f]) => f.length);
  if (!removed.length) return;
  try {
    const next = state.view === `folder:${folderId}` ? nextSelectionAfterRemoval(state.items, ids) : null;
    for (const [id, folders] of removed) await api.changeFolders([id], [], folders);
    await loadItems();
    if (next) select([next], { anchor: next });
    toast(`Removed ${plural(removed.length, 'item')} from “${folderById(folderId)?.name || 'folder'}” (still in your library)`, {
      action: { label: 'Undo', run: async () => { for (const [id, f] of removed) await api.changeFolders([id], f, []); await loadItems(); select(ids); } },
    });
  } catch (e) { toastError(e); }
}

/** Move = take out of one folder, put into another. */
export async function moveBetweenFolders(ids, fromId, toId) {
  if (!ids.length || !toId || fromId === toId) return;
  try {
    await api.changeFolders(ids, [toId], fromId ? [fromId] : []);
    await loadItems();
    toast(`Moved ${plural(ids.length, 'item')} to “${folderById(toId)?.name || 'folder'}”`, {
      type: 'success',
      action: { label: 'Undo', run: async () => { await api.changeFolders(ids, fromId ? [fromId] : [], [toId]); await loadItems(); } },
    });
  } catch (e) { toastError(e); }
}

/** Item pane: take one item out of one specific folder. */
export async function removeFolderMembership(id, folderId) {
  try {
    await api.changeFolders([id], [], [folderId]);
    await loadItems();
    toast(`Removed from “${folderById(folderId)?.name || 'folder'}”`, {
      action: { label: 'Undo', run: async () => { await api.changeFolders([id], [folderId], []); await loadItems(); } },
    });
  } catch (e) { toastError(e); }
}

export async function addTags(ids, names) {
  if (!ids.length || !names.length) return;
  try {
    await api.tagItems(ids, names, []);
    await loadItems();
    toast(`Tagged ${plural(ids.length, 'item')} with ${names.map((n) => `“${n}”`).join(', ')}`, { type: 'success' });
  } catch (e) { toastError(e); }
}

export async function removeTags(ids, names) {
  if (!ids.length || !names.length) return;
  try { await api.tagItems(ids, [], names); await loadItems(); } catch (e) { toastError(e); }
}

export async function updateTag(tag, data) {
  try {
    const t = await api.updateTag(tag.id, data);
    if (data.name && state.tagFilter.has(tag.name)) { state.tagFilter.delete(tag.name); state.tagFilter.add(t.name); }
    await loadItems();
    return t;
  } catch (e) { toastError(e); return null; }
}

export async function deleteTag(tag) {
  const { confirmDialog } = await import('./ui/dialog.js');
  if (!(await confirmDialog(`Delete the tag “${tag.name}”?`, {
    title: 'Delete tag', confirm: 'Delete Tag', danger: true,
    detail: tag.count ? `It is removed from ${plural(tag.count, 'item')}; the items themselves stay.` : '',
  }))) return;
  try { await api.deleteTag(tag.id); state.tagFilter.delete(tag.name); await loadItems(); } catch (e) { toastError(e); }
}

export async function deleteSmart(sf) {
  const { confirmDialog } = await import('./ui/dialog.js');
  if (!(await confirmDialog(`Delete the smart folder “${sf.name}”?`, { title: 'Delete smart folder', confirm: 'Delete', danger: true, detail: 'Only the saved search is removed; no items are deleted.' }))) return;
  try {
    await api.deleteSmart(sf.id);
    if (state.view === `smart:${sf.id}`) setView('library');
    await loadItems();
  } catch (e) { toastError(e); }
}
