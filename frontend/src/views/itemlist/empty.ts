// What the item list shows when it has no rows: why it is empty here, and what to do about it.
import * as A from '../../actions.js';
import { icon } from '../../icons.js';
import { currentFolderId, emit, folderById, state } from '../../store.js';
import { escapeHtml } from '../../util.js';
import { smartFolderDialog } from '../organizeDialogs.js';

export function emptyState(): HTMLElement {
  const el = document.createElement('div');
  el.className = 'list-empty';
  const smartFolder = () => state.sidebar.smart.find((s) => `smart:${s.id}` === state.view);
  if (state.query) {
    el.innerHTML = `<div class="empty-state">${icon('search')}<h3>No results</h3>
      <p>Nothing matches “${escapeHtml(state.query)}” in <b>${escapeHtml(state.settingsMeta.searchModes?.[state.mode] || state.mode)}</b>.</p>
      <div class="actions">${state.mode !== 'fulltext' ? '<button class="btn" data-empty="fulltext">Search full text too</button>' : ''}
      <button class="btn btn-ghost" data-empty="clear">Clear search</button></div></div>`;
  } else if (state.tagFilter.size) {
    el.innerHTML = `<div class="empty-state">${icon('tag')}<h3>No items with ${state.tagFilter.size > 1 ? 'all these tags' : 'this tag'} here</h3>
      <p>${[...state.tagFilter].map((t) => `“${escapeHtml(t)}”`).join(' + ')}</p><div class="actions"><button class="btn" data-empty="clear-tags">Clear tag filter</button></div></div>`;
  } else if (currentFolderId()) {
    el.innerHTML = `<div class="empty-state">${icon('folder')}<h3>${escapeHtml(folderById(currentFolderId()!)?.name || 'Folder')} is empty</h3>
      <p>Drag items onto the folder in the sidebar, use “Move to Folder…”, or add new items while this folder is open.</p>
      <div class="actions"><button class="btn btn-primary" data-empty="identifier">${icon('wand')} Add by Identifier</button><button class="btn" data-empty="new">${icon('plus')} New Item</button></div></div>`;
  } else if (state.view.startsWith('smart:')) {
    el.innerHTML = `<div class="empty-state">${icon('smartFolder')}<h3>Nothing matches yet</h3>
      <p>This smart folder shows items matching <code class="mono">${escapeHtml(smartFolder()?.query || '')}</code>.</p>
      <div class="actions"><button class="btn" data-empty="edit-smart">Edit Smart Folder…</button></div></div>`;
  } else if (state.view === 'duplicates') {
    el.innerHTML = `<div class="empty-state">${icon('check')}<h3>No duplicates</h3><p>No two items share a DOI, or the same title and year.</p></div>`;
  } else if (state.view === 'trash') {
    el.innerHTML = `<div class="empty-state">${icon('trash')}<h3>The Trash is empty</h3><p>Items you delete stay here until you empty the Trash.</p></div>`;
  } else {
    el.innerHTML = `<div class="empty-state">${icon('library')}<h3>Your library is empty</h3>
      <p>Add a paper by DOI, arXiv ID or ISBN, import a BibTeX/RIS/EndNote file, or drop PDFs here.</p>
      <div class="actions"><button class="btn btn-primary" data-empty="identifier">${icon('wand')} Add by Identifier</button>
      <button class="btn" data-empty="import">${icon('import')} Import…</button>
      <button class="btn" data-empty="new">${icon('plus')} New Item</button></div></div>`;
  }
  el.addEventListener('click', (e) => {
    const act = (e.target as Element).closest<HTMLElement>('[data-empty]')?.dataset.empty;
    if (act === 'fulltext') emit('set-search-mode', 'fulltext');
    if (act === 'clear') emit('clear-search');
    if (act === 'identifier') emit('open-identifier');
    if (act === 'import') emit('open-import');
    if (act === 'new') A.newItem();
    if (act === 'clear-tags') A.clearTagFilter();
    if (act === 'edit-smart') smartFolderDialog(smartFolder());
  });
  return el;
}
