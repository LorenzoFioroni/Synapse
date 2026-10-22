// User-level operations. Views call these; these talk to the API, update the
// store and emit events. Keeping them here keeps views free of API details.
import { byId, currentFolderId, emit, folderById, folderDescendants, selectedItems, state } from './store.js';
import { confirmDialog } from './ui/dialog.js';
import { progressToast, toast, toastError } from './ui/toast.js';
import type { Attachment, Counts, Item, Sidebar, SmartFolder, Tag } from './types.js';
import { plural } from './util.js';
import { type ApiError, api, openAttachment } from './api.js';

let loadSeq = 0;

export async function loadItems({ keepSelection = true } = {}): Promise<void> {
  const seq = ++loadSeq;
  state.loading = true;
  emit('loading', true);
  try {
    let res;
    try {
      res = await api.listItems({ view: state.view, q: state.query, mode: state.mode, tags: [...state.tagFilter] });
    } catch (e) {
      // the folder / smart folder shown was deleted (e.g. in another tab): fall back to the library
      if ((e as ApiError).status === 404 && state.view !== 'library') { state.view = 'library'; emit('view'); return loadItems({ keepSelection: false }); }
      throw e;
    }
    if (seq !== loadSeq) return;           // a newer request superseded this one
    for (const it of res.items) it._lean = true;   // no abstract: the item pane fetches it
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
      const before = [...state.selection];
      state.selection = new Set(before.filter((id) => ids.has(id)));
      if (before.length && !state.selection.size) {
        // the selected items left the list (dismissed from a plugin's list, trashed elsewhere):
        // select the one after them, as trashing here does
        const order = state.order || [];
        const last = Math.max(...before.map((id) => order.indexOf(id)));
        const next = last >= 0 ? order.slice(last + 1).find((id) => ids.has(id)) || order.slice(0, last).reverse().find((id) => ids.has(id)) : undefined;
        if (next) { state.selection.add(next); state.anchor = next; state.cursor = next; }
      }
    } else state.selection.clear();
    emit('items');
    emit('selection');
  } catch (e) {
    toastError(e);
  } finally {
    if (seq === loadSeq) { state.loading = false; emit('loading', false); }
  }
}

export function setView(view: string): void {
  if (state.view === view) return;
  state.view = view;
  state.selection.clear();
  state.tagFilter.clear();
  emit('view');
  loadItems({ keepSelection: false });
}

export function setSearch(query: string, mode = state.mode): void {
  state.query = query;
  state.mode = mode;
  emit('search');
  loadItems();
}

export function select(ids: string[], { anchor, cursor }: { anchor?: string | null; cursor?: string | null } = {}): void {
  state.selection = new Set(ids);
  if (anchor !== undefined) state.anchor = anchor;
  state.cursor = cursor !== undefined ? cursor : (ids[ids.length - 1] ?? null);
  emit('selection');
}

/** Replace one item in the store after an edit (no full reload). */
export function patchLocal(item: Item): void {
  const i = state.items.findIndex((x) => x.id === item.id);
  if (i >= 0) {
    state.items[i] = { ...state.items[i], ...item };
    if ('abstract' in item) delete state.items[i]._lean;   // the whole item came back
  }
  emit('item-updated', item.id);
}

/** In this view, can changed items be patched in place, or might they leave or join the list? */
function patchable() {
  return state.view === 'library' && !state.query && !state.tagFilter.size;
}

function applyCounts(res: { counts: Counts; sidebar?: Sidebar }): void {
  state.counts = res.counts;
  state.sidebar = res.sidebar || state.sidebar;
  emit('sidebar');
}

/**
 * Some items changed (tags, folders, attachments, keys, metadata): show their new state without
 * reloading the whole list — fetch just them. Where the change could make an item leave or join
 * the list (a folder, a search, a tag filter, a smart folder), reload the list instead.
 * New items (`added`) are inserted when the list is the plain library.
 */
export async function refreshItems(ids: (string | null | undefined)[], { added = false } = {}): Promise<void> {
  const wanted = [...new Set(ids)].filter((id): id is string => !!id);
  if (!wanted.length) return;
  if (!patchable() || wanted.length > 300) return loadItems();
  try {
    const res = await api.itemsByIds(wanted);
    for (const fresh of res.items) {
      fresh._lean = true;
      const i = state.items.findIndex((x) => x.id === fresh.id);
      if (fresh.deleted_at) { if (i >= 0) state.items.splice(i, 1); continue; }
      if (i >= 0) state.items[i] = fresh; else if (added) state.items.push(fresh);
    }
    const gone = new Set(res.missing || []);
    if (gone.size) state.items = state.items.filter((x) => !gone.has(x.id));
    applyCounts(res);
    emit('items');
    emit('selection');
  } catch (e) { toastError(e); }
}

/** Items left this list (trashed, restored from the Trash, deleted): drop them here. */
async function removeLocal(ids: string[]): Promise<void> {
  const gone = new Set(ids);
  state.items = state.items.filter((x) => !gone.has(x.id));
  state.selection = new Set([...state.selection].filter((id) => !gone.has(id)));
  emit('items');
  emit('selection');
  try { applyCounts(await api.itemsByIds([])); } catch { /* counts catch up on the next load */ }
}

export async function updateField(id: string, field: string, value: unknown): Promise<Item> {
  try {
    const item = await api.updateItem(id, { [field]: value });
    patchLocal(item);
    return item;
  } catch (e) { toastError(e); throw e; }
}

export async function newItem(data: Partial<Item> = {}): Promise<Item | undefined> {
  try {
    if (state.view !== 'library' && !currentFolderId()) setView('library');
    const item = await api.createItem({ item_type: 'journalArticle', folder_id: currentFolderId(), ...data });
    await refreshItems([item.id], { added: true });
    select([item.id], { anchor: item.id });
    emit('focus-field', { id: item.id, field: 'title' });
    return item;
  } catch (e) { toastError(e); }
}

export async function trashSelected(): Promise<void> {
  const items = selectedItems().filter((i) => !i._outside);   // papers from a plugin's list are not in the library
  if (!items.length) return;
  const ids = items.map((i) => i.id);
  if (state.view === 'trash') return deleteForever(ids);
  if (state.settings.confirm_trash && !(await confirmDialog(`Move ${plural(ids.length, 'item')} to the Trash?`, { confirm: 'Move to Trash' }))) return;
  try {
    const sorted = [...state.items];
    const next = nextSelectionAfterRemoval(sorted, ids);
    await api.trash(ids);
    await removeLocal(ids);
    if (next) select([next], { anchor: next });
    toast(`Moved ${plural(ids.length, 'item')} to the Trash`, {
      action: { label: 'Undo', run: async () => { await api.restore(ids); await refreshItems(ids, { added: true }); select(ids); } },
    });
  } catch (e) { toastError(e); }
}

function nextSelectionAfterRemoval(items: Item[], ids: string[]): string | null {
  const order = state.order || items.map((i) => i.id);   // display order (rows off screen are not in the DOM)
  const set = new Set(ids);
  const last = Math.max(...ids.map((id) => order.indexOf(id)));
  for (let i = last + 1; i < order.length; i++) if (!set.has(order[i])) return order[i];
  for (let i = last - 1; i >= 0; i--) if (!set.has(order[i])) return order[i];
  return null;
}

/** Duplicates view: keep one item of a group and move the others to the Trash. */
export async function keepOnly(id: string): Promise<void> {
  const keep = byId(id);
  if (!keep || keep.dup_group === undefined) return;
  const others = state.items.filter((i) => i.dup_group === keep.dup_group && i.id !== id).map((i) => i.id);
  if (!others.length) return;
  try {
    await api.trash(others);
    await removeLocal(others);
    toast(`Kept “${(keep.title || 'item').slice(0, 50)}”, moved ${plural(others.length, 'duplicate')} to the Trash`, {
      type: 'success',
      action: { label: 'Undo', run: async () => { await api.restore(others); await loadItems(); } },
    });
  } catch (e) { toastError(e); }
}

export async function restoreSelected(): Promise<void> {
  const ids = [...state.selection];
  if (!ids.length) return;
  try {
    await api.restore(ids);
    await removeLocal(ids);   // they leave the Trash
    toast(`Restored ${plural(ids.length, 'item')} to My Library`, { type: 'success' });
  } catch (e) { toastError(e); }
}

export async function deleteForever(ids: string[]): Promise<void> {
  if (!(await confirmDialog(`Permanently delete ${plural(ids.length, 'item')}?`,
    { title: 'Delete permanently', confirm: 'Delete', danger: true, detail: 'Attached files are deleted too. This cannot be undone.' }))) return;
  try {
    await api.deleteForever(ids);
    await removeLocal(ids);
    toast(`Deleted ${plural(ids.length, 'item')}`);
  } catch (e) { toastError(e); }
}

export async function emptyTrash(): Promise<void> {
  const n = state.counts.trash;
  if (!n) return;
  if (!(await confirmDialog(`Permanently delete all ${plural(n, 'item')} in the Trash?`,
    { title: 'Empty Trash', confirm: 'Empty Trash', danger: true, detail: 'Attached files are deleted too. This cannot be undone.' }))) return;
  try {
    await api.emptyTrash();
    await loadItems();
    toast('Trash emptied');
  } catch (e) { toastError(e); }
}

/** Open an item's PDF (``att``, else its first attachment), or its web page when it has none. */
export function openPdf(itemOrId: Item | string | undefined, att?: Attachment): void {
  const item = typeof itemOrId === 'string' ? byId(itemOrId) : itemOrId;
  if (!item) return;
  const a = att || (item.attachments || [])[0];
  if (a) openAttachment(item, a);
  else if (item.url) window.open(item.url, '_blank');
  else toast('This item has no PDF yet. Try “Find Available PDF” or add one.', { timeout: 5000 });
}

export async function findPdf(id: string): Promise<void> {
  const item = byId(id);
  const close = toast(`Searching for a PDF of “${(item?.title || 'item').slice(0, 60)}”…`, { timeout: 0 });
  try {
    const res = await api.findPdf(id);
    close();
    if (res.status === 'success') {
      toast('PDF found and attached', { type: 'success' });
      state.expanded.add(id);
      await refreshItems([id]);
      emit('items');
    } else toast(res.message || 'No PDF found', { type: 'error' });
  } catch (e) { close(); toastError(e); }
}

/**
 * Find PDFs for several items, one after another (gentle on Unpaywall/arXiv).
 * Items that already have an attachment are skipped. Cancellable.
 */
export async function findPdfs(ids: string[], { auto = false } = {}): Promise<void> {
  const items = ids.map(byId).filter((i): i is Item => !!i);
  const todo = items.filter((i) => !(i.attachments || []).length);
  const skipped = items.length - todo.length;
  if (!todo.length) { if (!auto) toast(`All ${plural(items.length, 'selected item')} already have a PDF`); return; }
  let cancelled = false;
  const pt = progressToast('Finding PDFs…', { onCancel: () => { cancelled = true; } });
  const found: string[] = [], missing: string[] = [], failed: string[] = [];
  for (let n = 0; n < todo.length && !cancelled; n++) {
    const it = todo[n];
    pt.set(`Finding PDFs ${n + 1}/${todo.length}: ${(it.title || 'Untitled').slice(0, 60)}`, n, todo.length);
    try {
      const res = await api.findPdf(it.id);
      (res.status === 'success' ? found : missing).push(it.id);
      if (res.status === 'success') await refreshItems([it.id]);
    } catch (e) {
      failed.push(it.id);
      if ((e as ApiError).status === 0) break;      // server gone: stop instead of failing every item
    }
    if (n < todo.length - 1) await new Promise((r) => setTimeout(r, 400));
  }
  pt.close();
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

export async function uploadAttachments(id: string, files: Iterable<File>): Promise<void> {
  const pdfs = [...files].filter((f) => /pdf$/i.test(f.type) || /\.pdf$/i.test(f.name));
  if (!pdfs.length) { toast('Only PDF files can be attached', { type: 'error' }); return; }
  try {
    await api.uploadAttachments(id, pdfs);
    state.expanded.add(id);
    await refreshItems([id]);
    toast(`Attached ${plural(pdfs.length, 'file')}`, { type: 'success' });
  } catch (e) { toastError(e); }
}

export async function addPdfsAsItems(files: Iterable<File>): Promise<void> {
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
    const msg = found === pdfs.length ? `Added ${plural(pdfs.length, 'item')} with metadata`
      : `Added ${plural(pdfs.length, 'item')} · metadata found for ${found}`;
    // Settings → Adding Items → reference lists: "always" is done by the server, "ask" asks here
    const withId = res.created.filter((c) => !c.note && (c.item.doi || c.item.archive_id)).map((c) => c.item.id);
    const ask = state.settings.add_fetch_references === 'ask' && withId.length;
    toast(msg, {
      type: 'success',
      timeout: ask ? 10000 : undefined,
      action: ask ? { label: 'Fetch References', run: () => api.queueReferences(withId).then(() => toast(`Fetching ${plural(withId.length, 'reference list')} in the background`)) } : null,
    });
  } catch (e) { close(); toastError(e); }
}

export async function regenerateCitekeys(ids: string[]): Promise<void> {
  try {
    const res = await api.regenerateCitekeys(ids);
    await refreshItems(ids);
    const keys = Object.values(res.keys);
    toast(keys.length === 1 ? `Citation key: ${keys[0]}` : `Regenerated ${plural(keys.length, 'citation key')}`, { type: 'success' });
  } catch (e) { toastError(e); }
}

/* ============================================================ organisation */
export function toggleTagFilter(name: string, only = false): void {
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

export async function createFolder(name: string, parentId: number | null = null, { open = true } = {}) {
  try {
    const f = await api.createFolder(name, parentId);
    if (parentId) { state.openFolders.add(parentId); }
    await loadItems();
    if (open) setView(`folder:${f.id}`);
    return f;
  } catch (e) { toastError(e); return null; }
}

export async function renameFolder(id: number, name: string): Promise<boolean> {
  try { await api.updateFolder(id, { name }); await loadItems(); return true; } catch (e) { toastError(e); return false; }
}

export async function moveFolder(id: number, parentId: number | null): Promise<void> {
  try {
    await api.updateFolder(id, { parent_id: parentId });
    if (parentId) state.openFolders.add(parentId);
    await loadItems();
  } catch (e) { toastError(e); }
}

export async function deleteFolder(id: number): Promise<void> {
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
      for (const x of state.sidebar.folders) if (x.parent_id !== null && gone.has(x.parent_id) && !gone.has(x.id)) { gone.add(x.id); grew = true; }
    }
    await api.deleteFolder(id);
    const shown = currentFolderId();
    if (shown !== null && gone.has(shown)) setView('library');
    await loadItems();
    toast(`Deleted “${f.name}”`);
  } catch (e) { toastError(e); }
}

// An item can be in several folders. Adding links it to a folder (its data is stored
// once); removing unlinks it and never deletes the item.
export async function addToFolder(ids: string[], folderId: number, { quiet = false } = {}): Promise<void> {
  if (!ids.length || !folderId) return;
  const before = new Set(ids.filter((id) => byId(id)?.folder_ids?.includes(folderId)));
  try {
    await api.changeFolders(ids, [folderId], []);
    await refreshItems(ids);
    if (quiet) return;
    const added = ids.filter((id) => !before.has(id));
    const name = folderById(folderId)?.name || 'folder';
    if (!added.length) { toast(`Already in “${name}”`); return; }
    toast(`Added ${plural(added.length, 'item')} to “${name}”`, {
      type: 'success',
      action: { label: 'Undo', run: async () => { await api.changeFolders(added, [], [folderId]); await refreshItems(added); } },
    });
  } catch (e) { toastError(e); }
}

/**
 * Take items out of a folder. When the folder view also shows subfolder items,
 * they are taken out of those subfolders too, so they disappear from the view.
 */
export async function removeFromFolder(ids: string[], folderId: number): Promise<void> {
  if (!ids.length || !folderId) return;
  const scope = new Set(state.settings.show_subfolder_items !== false ? folderDescendants(folderId) : [folderId]);
  const removed = ids.map((id): [string, number[]] => [id, (byId(id)?.folder_ids || []).filter((f) => scope.has(f))]).filter(([, f]) => f.length);
  if (!removed.length) return;
  try {
    const next = state.view === `folder:${folderId}` ? nextSelectionAfterRemoval(state.items, ids) : null;
    for (const [id, folders] of removed) await api.changeFolders([id], [], folders);
    await refreshItems(ids);
    if (next) select([next], { anchor: next });
    toast(`Removed ${plural(removed.length, 'item')} from “${folderById(folderId)?.name || 'folder'}” (still in your library)`, {
      action: { label: 'Undo', run: async () => { for (const [id, f] of removed) await api.changeFolders([id], f, []); await refreshItems(ids); select(ids); } },
    });
  } catch (e) { toastError(e); }
}

/** Move = take out of one folder, put into another. */
export async function moveBetweenFolders(ids: string[], fromId: number | null, toId: number): Promise<void> {
  if (!ids.length || !toId || fromId === toId) return;
  try {
    await api.changeFolders(ids, [toId], fromId ? [fromId] : []);
    await refreshItems(ids);
    toast(`Moved ${plural(ids.length, 'item')} to “${folderById(toId)?.name || 'folder'}”`, {
      type: 'success',
      action: { label: 'Undo', run: async () => { await api.changeFolders(ids, fromId ? [fromId] : [], [toId]); await refreshItems(ids); } },
    });
  } catch (e) { toastError(e); }
}

/** Item pane: take one item out of one specific folder. */
export async function removeFolderMembership(id: string, folderId: number): Promise<void> {
  try {
    await api.changeFolders([id], [], [folderId]);
    await refreshItems([id]);
    toast(`Removed from “${folderById(folderId)?.name || 'folder'}”`, {
      action: { label: 'Undo', run: async () => { await api.changeFolders([id], [folderId], []); await refreshItems([id]); } },
    });
  } catch (e) { toastError(e); }
}

export async function addTags(ids: string[], names: string[]): Promise<void> {
  if (!ids.length || !names.length) return;
  try {
    await api.tagItems(ids, names, []);
    await refreshItems(ids);
    toast(`Tagged ${plural(ids.length, 'item')} with ${names.map((n) => `“${n}”`).join(', ')}`, { type: 'success' });
  } catch (e) { toastError(e); }
}

export async function removeTags(ids: string[], names: string[]): Promise<void> {
  if (!ids.length || !names.length) return;
  try { await api.tagItems(ids, [], names); await refreshItems(ids); } catch (e) { toastError(e); }
}

export async function updateTag(tag: Tag, data: { name?: string; color?: string | null }): Promise<Tag | null> {
  try {
    const t = await api.updateTag(tag.id, data);
    if (data.name && state.tagFilter.has(tag.name)) { state.tagFilter.delete(tag.name); state.tagFilter.add(t.name); }
    await loadItems();
    return t;
  } catch (e) { toastError(e); return null; }
}

export async function deleteTag(tag: Tag): Promise<void> {
  const { confirmDialog } = await import('./ui/dialog.js');
  if (!(await confirmDialog(`Delete the tag “${tag.name}”?`, {
    title: 'Delete tag', confirm: 'Delete Tag', danger: true,
    detail: tag.count ? `It is removed from ${plural(tag.count, 'item')}; the items themselves stay.` : '',
  }))) return;
  try { await api.deleteTag(tag.id); state.tagFilter.delete(tag.name); await loadItems(); } catch (e) { toastError(e); }
}

export async function deleteSmart(sf: SmartFolder): Promise<void> {
  const { confirmDialog } = await import('./ui/dialog.js');
  if (!(await confirmDialog(`Delete the smart folder “${sf.name}”?`, { title: 'Delete smart folder', confirm: 'Delete', danger: true, detail: 'Only the saved search is removed; no items are deleted.' }))) return;
  try {
    await api.deleteSmart(sf.id);
    if (state.view === `smart:${sf.id}`) setView('library');
    await loadItems();
  } catch (e) { toastError(e); }
}

/**
 * Add papers from a plugin's list (outside the library) to the library, into `folderId` when
 * given: the server looks each up by its DOI or arXiv id. In the list they become the library
 * items. What Settings → Adding Items says to do afterwards runs too ("ask" counts as yes: the
 * "+" is one click, with nowhere to ask).
 */
export async function addOutside(items: Item[], folderId: number | null = null): Promise<void> {
  const papers = items.filter((i) => i._outside);
  if (!papers.length) return;
  const pt = papers.length > 1 ? progressToast(`Adding ${plural(papers.length, 'paper')}…`) : null;
  const close = pt ? null : toast(`Adding “${(papers[0].title || 'paper').slice(0, 60)}”…`, { timeout: 0 });
  const added: string[] = [];
  let already = 0;
  for (const [n, p] of papers.entries()) {
    pt?.set(`Adding ${n + 1}/${papers.length}: ${(p.title || 'Untitled').slice(0, 60)}`, n, papers.length);
    try {
      const res = await api.addOutside(p, folderId);
      if (res.duplicate) already++; else added.push(res.item.id);
    } catch (e) {
      toastError(e);
      if ((e as ApiError).status === 0) break;      // server gone
    }
  }
  pt?.close();
  close?.();
  await loadItems();
  if (added.length) select(added, { anchor: added[0], cursor: added[0] });
  const { mode, runAfterAdd } = await import('./afterAdd.js');
  runAfterAdd(added, { pdf: mode('pdf') !== 'never', references: mode('references') !== 'never' });
  const where = folderId ? `“${folderById(folderId)?.name || 'folder'}”` : 'My Library';
  const parts = added.length ? [added.length === 1 && papers.length === 1 ? `Added to ${where}` : `Added ${plural(added.length, 'paper')} to ${where}`] : [];
  if (already) parts.push(papers.length === 1 ? 'Already in your library' : `${already} already in your library`);
  if (!parts.length) return;
  toast(parts.join(' · '), {
    type: added.length ? 'success' : 'info',
    action: added.length ? { label: 'Undo', run: async () => { await api.trash(added); await loadItems(); } } : null,
  });
}
