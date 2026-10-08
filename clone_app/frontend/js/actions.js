// User-level operations. Views call these; these talk to the API, update the
// store and emit events. Keeping them here keeps views free of API details.
import { api, openAttachment } from './api.js';
import { byId, emit, selectedItems, state } from './store.js';
import { confirmDialog } from './ui/dialog.js';
import { toast, toastError } from './ui/toast.js';
import { plural } from './util.js';

let loadSeq = 0;

export async function loadItems({ keepSelection = true } = {}) {
  const seq = ++loadSeq;
  state.loading = true;
  emit('loading', true);
  try {
    const res = await api.listItems({ view: state.view, q: state.query, mode: state.mode });
    if (seq !== loadSeq) return;           // a newer request superseded this one
    state.items = res.items;
    state.hits = res.hits || {};
    state.counts = res.counts;
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
    if (state.view !== 'library') setView('library');
    const item = await api.createItem({ item_type: state.settings.default_item_type || 'journalArticle', ...data });
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
    if (state.view !== 'library') setView('library');
    const res = await api.fromPdfs(pdfs);
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
