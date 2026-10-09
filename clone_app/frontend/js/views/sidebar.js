// Left sidebar: library views, folder tree, smart folders and the tag pane.
//
// Drag & drop: drop items on a folder to move them there (an item lives in one
// folder), on a tag to tag them, on Unfiled to take them out of their folder,
// on Trash to delete them. Drag a folder onto another to nest it, or onto the
// "Folders" heading to move it to the top level. PDFs dropped on a folder are
// added as new items in that folder.
import * as A from '../actions.js';
import { icon } from '../icons.js';
import { folderById, folderTotal, folderTree, on, saveOpenFolders, state } from '../store.js';
import { promptDialog } from '../ui/dialog.js';
import { openMenu } from '../ui/menu.js';
import { escapeHtml, plural, storage, store } from '../util.js';
import { openExport } from './exportDialog.js';
import { smartFolderDialog } from './organizeDialogs.js';

export const ITEMS_MIME = 'application/x-zc-items';
const FOLDER_MIME = 'application/x-zc-folder';
const TAG_COLORS = ['#e5484d', '#f76b15', '#ffc53d', '#30a46c', '#12a594', '#0090ff', '#6e56cf', '#d6409f', '#8d8d86'];

let root, tree, smartList, tagCloud, tagFilterInput;
let showAllTags = storage('zc.showAllTags', false);
let renaming = null;

export function initSidebar() {
  root = document.getElementById('sidebar');
  root.innerHTML = `
    <div class="side-scroll">
      <div class="side-section">Library</div>
      <button class="side-item" data-view="library">${icon('library')}<span class="nm">My Library</span><span class="count" data-count="library"></span></button>
      <button class="side-item" data-view="unfiled" data-drop="unfiled" title="Items that are not in any folder">${icon('unfiled')}<span class="nm">Unfiled Items</span><span class="count" data-count="unfiled"></span></button>
      <button class="side-item" data-view="duplicates" title="Items with the same DOI, or the same title and year">${icon('copy')}<span class="nm">Duplicate Items</span><span class="count" data-count="duplicates"></span></button>
      <button class="side-item" data-view="trash" data-drop="trash">${icon('trash')}<span class="nm">Trash</span><span class="count" data-count="trash"></span></button>

      <div class="side-section with-action" data-drop="folder-root">Folders
        <button class="icon-btn side-add" data-act="new-folder" title="New folder">${icon('plus', 'icon-sm')}</button></div>
      <div class="folder-tree" role="tree"></div>

      <div class="side-section with-action">Smart Folders
        <button class="icon-btn side-add" data-act="new-smart" title="New smart folder (saved search)">${icon('plus', 'icon-sm')}</button></div>
      <div class="smart-list"></div>
    </div>
    <div class="tag-pane">
      <div class="tag-head"><span>Tags</span>
        <input class="tag-filter" placeholder="Filter tags" spellcheck="false">
        <button class="icon-btn" data-act="tag-menu" title="Tag options">${icon('drag', 'icon-sm')}</button></div>
      <div class="tag-cloud"></div>
    </div>
    <div class="side-footer"><div class="row" data-index-status></div></div>`;
  tree = root.querySelector('.folder-tree');
  smartList = root.querySelector('.smart-list');
  tagCloud = root.querySelector('.tag-cloud');
  tagFilterInput = root.querySelector('.tag-filter');

  root.addEventListener('click', onClick);
  root.addEventListener('dblclick', onDblClick);
  root.addEventListener('contextmenu', onContextMenu);
  tagFilterInput.addEventListener('input', paintTags);
  bindDnD();

  on('items', paint);
  on('sidebar', paint);
  on('view', paint);
  on('tag-filter', paintTags);
  on('indexer', paintIndexer);
  paint();
}

/* ================================================================= paint */
function paint() {
  root.querySelectorAll('[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === state.view));
  for (const k of ['library', 'unfiled', 'trash', 'duplicates']) {
    root.querySelector(`[data-count="${k}"]`).textContent = state.counts[k] || '';
  }
  paintFolders();
  paintSmart();
  paintTags();
}

function paintFolders() {
  if (renaming) return;   // don't destroy an inline rename in progress
  const rows = folderTree();
  // hide children of collapsed folders
  const hidden = new Set();
  const visible = rows.filter((f) => {
    if (f.parent_id && (hidden.has(f.parent_id) || !state.openFolders.has(f.parent_id))) { hidden.add(f.id); return false; }
    return true;
  });
  tree.innerHTML = visible.map((f) => `
    <div class="side-item folder ${state.view === `folder:${f.id}` ? 'active' : ''}" role="treeitem" tabindex="-1"
         data-view="folder:${f.id}" data-folder="${f.id}" data-drop="folder" draggable="true" style="padding-left:${10 + f.depth * 14}px">
      <span class="twisty ${f.hasChildren ? '' : 'none'} ${state.openFolders.has(f.id) ? 'open' : ''}" data-act="toggle">${icon('chevron', 'icon-sm')}</span>
      ${icon('folder')}<span class="nm">${escapeHtml(f.name)}</span><span class="count">${folderTotal(f.id) || ''}</span>
    </div>`).join('') || '<div class="side-empty">Drag items here after creating a folder with <b>+</b>.</div>';
}

function paintSmart() {
  smartList.innerHTML = state.sidebar.smart.map((s) => `
    <div class="side-item ${state.view === `smart:${s.id}` ? 'active' : ''}" data-view="smart:${s.id}" data-smart="${s.id}" title="${escapeHtml(s.query)}">
      ${icon('smartFolder')}<span class="nm">${escapeHtml(s.name)}</span></div>`).join('')
    || '<div class="side-empty">Save a search as a smart folder with <b>+</b>.</div>';
}

function paintTags() {
  const q = tagFilterInput.value.trim().toLowerCase();
  // counts within the items currently shown (Zotero-style drill-down)
  const inView = {};
  for (const it of state.items) for (const t of it.tags || []) inView[t] = (inView[t] || 0) + 1;
  const tags = state.sidebar.tags.filter((t) => {
    if (q && !t.name.toLowerCase().includes(q)) return false;
    return showAllTags || inView[t.name] || t.color || state.tagFilter.has(t.name);
  });
  const coloured = tags.filter((t) => t.color);
  const plain = tags.filter((t) => !t.color);
  const chip = (t) => `<button class="tag-pill ${state.tagFilter.has(t.name) ? 'on' : ''} ${inView[t.name] ? '' : 'dim'}" data-tag="${t.id}" data-drop="tag"
      title="${escapeHtml(t.name)} · ${plural(inView[t.name] || 0, 'item')} here, ${t.count} in library">
      ${t.color ? `<span class="tag-dot" style="background:${t.color}"></span>` : ''}${escapeHtml(t.name)}<span class="n">${inView[t.name] || ''}</span></button>`;
  tagCloud.innerHTML = (state.tagFilter.size ? `<button class="tag-clear" data-act="clear-tags">${icon('close', 'icon-sm')} Clear tag filter (${state.tagFilter.size})</button>` : '')
    + (coloured.length ? `<div class="tag-group">${coloured.map(chip).join('')}</div>` : '')
    + `<div class="tag-group">${plain.map(chip).join('')}</div>`
    + (!tags.length ? `<div class="side-empty">${state.sidebar.tags.length ? 'No tags here.' : 'No tags yet. Add tags in the item pane, or drag items onto a tag.'}</div>` : '');
}

function paintIndexer() {
  const ix = state.indexer;
  const el = root.querySelector('[data-index-status]');
  if (!ix) { el.innerHTML = ''; return; }
  if (ix.indexer.running) {
    el.innerHTML = `<span class="spinner"></span> Indexing PDFs ${ix.indexer.done}/${ix.indexer.total}`;
  } else {
    el.innerHTML = `${icon('text', 'icon-sm')} ${ix.fulltext.documents}/${ix.attachments} PDFs searchable`;
    el.title = 'Full-text index status (Settings → Search & Full Text)';
  }
}

/* ============================================================ interaction */
const tagById = (id) => state.sidebar.tags.find((t) => t.id === Number(id));

function onClick(e) {
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act === 'toggle') {
    const id = Number(e.target.closest('[data-folder]').dataset.folder);
    state.openFolders.has(id) ? state.openFolders.delete(id) : state.openFolders.add(id);
    saveOpenFolders();
    paintFolders();
    return;
  }
  if (act === 'new-folder') return newFolder(null);
  if (act === 'new-smart') return smartFolderDialog(null, state.query ? { query: state.query, mode: state.mode } : {});
  if (act === 'clear-tags') return A.clearTagFilter();
  if (act === 'tag-menu') {
    return openMenu([
      { label: 'Show tags of all items', checked: showAllTags, run: () => { showAllTags = !showAllTags; store('zc.showAllTags', showAllTags); paintTags(); } },
      { label: 'Clear tag filter', icon: 'close', disabled: !state.tagFilter.size, run: A.clearTagFilter },
    ], e.target.closest('[data-act]'));
  }
  const pill = e.target.closest('[data-tag]');
  if (pill) {
    const t = tagById(pill.dataset.tag);
    if (t) A.toggleTagFilter(t.name);
    return;
  }
  const v = e.target.closest('[data-view]');
  if (v && !e.target.closest('input')) A.setView(v.dataset.view);
}

function onDblClick(e) {
  const f = e.target.closest('[data-folder]');
  if (f && !e.target.closest('[data-act]')) startRename(Number(f.dataset.folder));
  const s = e.target.closest('[data-smart]');
  if (s) smartFolderDialog(state.sidebar.smart.find((x) => x.id === Number(s.dataset.smart)));
}

function onContextMenu(e) {
  const at = { x: e.clientX, y: e.clientY };
  const f = e.target.closest('[data-folder]');
  const s = e.target.closest('[data-smart]');
  const t = e.target.closest('[data-tag]');
  const trash = e.target.closest('[data-view="trash"]');
  if (!f && !s && !t && !trash) return;
  e.preventDefault();
  if (f) {
    const folder = folderById(f.dataset.folder);
    openMenu([
      { label: 'New Subfolder', icon: 'folderPlus', run: () => newFolder(folder.id) },
      { label: 'Rename…', icon: 'edit', run: () => startRename(folder.id) },
      folder.parent_id ? { label: 'Move to Top Level', icon: 'arrowLeft', run: () => A.moveFolder(folder.id, null) } : null,
      'sep',
      { label: 'Save Folder as Smart Folder…', icon: 'smartFolder', run: () => smartFolderDialog(null, { name: folder.name, query: `folder:"${folderPathString(folder.id)}"` }) },
      { label: 'Export Folder…', icon: 'export', run: async () => { A.setView(`folder:${folder.id}`); await A.loadItems(); openExport(state.items.map((i) => i.id)); } },
      'sep',
      { label: 'Delete Folder…', icon: 'trash', danger: true, run: () => A.deleteFolder(folder.id) },
    ], at);
  } else if (s) {
    const sf = state.sidebar.smart.find((x) => x.id === Number(s.dataset.smart));
    openMenu([
      { label: 'Edit Smart Folder…', icon: 'edit', run: () => smartFolderDialog(sf) },
      'sep',
      { label: 'Delete Smart Folder…', icon: 'trash', danger: true, run: () => A.deleteSmart(sf) },
    ], at);
  } else if (t) {
    const tag = tagById(t.dataset.tag);
    openMenu([
      { label: state.tagFilter.has(tag.name) ? 'Remove from Filter' : 'Filter by This Tag', icon: 'search', run: () => A.toggleTagFilter(tag.name) },
      { label: 'Show Only This Tag', icon: 'tag', run: () => A.toggleTagFilter(tag.name, true) },
      'sep',
      { label: 'Rename Tag…', icon: 'edit', run: async () => {
        const name = await promptDialog('New name', { title: 'Rename Tag', value: tag.name, confirm: 'Rename', hint: 'Renaming onto an existing tag merges the two.' });
        if (name && name !== tag.name) A.updateTag(tag, { name });
      } },
      { section: 'Colour' },
      ...TAG_COLORS.map((c) => ({ label: colorName(c), checked: tag.color === c, run: () => A.updateTag(tag, { color: c }) })),
      { label: 'No Colour', checked: !tag.color, run: () => A.updateTag(tag, { color: null }) },
      'sep',
      { label: 'Delete Tag…', icon: 'trash', danger: true, run: () => A.deleteTag(tag) },
    ], at);
  } else if (trash) {
    openMenu([{ label: 'Empty Trash…', icon: 'trash', danger: true, disabled: !state.counts.trash, run: A.emptyTrash }], at);
  }
}

function colorName(c) {
  return { '#e5484d': '● Red', '#f76b15': '● Orange', '#ffc53d': '● Yellow', '#30a46c': '● Green', '#12a594': '● Teal', '#0090ff': '● Blue', '#6e56cf': '● Purple', '#d6409f': '● Pink', '#8d8d86': '● Grey' }[c] || c;
}

function folderPathString(id) {
  const names = [];
  let f = folderById(id);
  while (f) { names.unshift(f.name); f = folderById(f.parent_id); }
  return names.join('/');
}

async function newFolder(parentId) {
  // unique default name among siblings, then rename inline
  const siblings = new Set(state.sidebar.folders.filter((f) => (f.parent_id ?? null) === parentId).map((f) => f.name.toLowerCase()));
  let name = 'New Folder', n = 2;
  while (siblings.has(name.toLowerCase())) name = `New Folder ${n++}`;
  const f = await A.createFolder(name, parentId, { open: true });
  if (f) setTimeout(() => startRename(f.id), 60);
}

function startRename(id) {
  const row = tree.querySelector(`[data-folder="${id}"]`);
  const f = folderById(id);
  if (!row || !f) return;
  renaming = id;
  const nm = row.querySelector('.nm');
  const input = document.createElement('input');
  input.className = 'rename-input';
  input.value = f.name;
  nm.replaceWith(input);
  row.draggable = false;
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    renaming = null;
    const v = input.value.trim();
    if (save && v && v !== f.name) { if (!(await A.renameFolder(id, v))) paintFolders(); } else paintFolders();
  };
  input.addEventListener('keydown', (e) => {
    e.stopPropagation();
    if (e.key === 'Enter') finish(true);
    if (e.key === 'Escape') finish(false);
  });
  input.addEventListener('blur', () => finish(true));
  input.addEventListener('click', (e) => e.stopPropagation());
}

/* ============================================================ drag & drop */
function dragKind(e) {
  const types = [...(e.dataTransfer?.types || [])];
  if (types.includes(ITEMS_MIME)) return 'items';
  if (types.includes(FOLDER_MIME)) return 'folder';
  if (types.includes('Files')) return 'files';
  return null;
}

function accepts(target, kind) {
  const d = target.dataset.drop;
  if (kind === 'items') return ['folder', 'tag', 'unfiled', 'trash'].includes(d);
  if (kind === 'folder') return ['folder', 'folder-root'].includes(d);
  if (kind === 'files') return d === 'folder';
  return false;
}

function bindDnD() {
  root.addEventListener('dragstart', (e) => {
    const f = e.target.closest('[data-folder]');
    if (!f || renaming) return;
    e.dataTransfer.setData(FOLDER_MIME, f.dataset.folder);
    e.dataTransfer.effectAllowed = 'move';
    f.classList.add('dragging');
  });
  root.addEventListener('dragend', () => root.querySelectorAll('.dragging, .drop-target').forEach((x) => x.classList.remove('dragging', 'drop-target')));
  root.addEventListener('dragover', (e) => {
    const kind = dragKind(e);
    const target = e.target.closest('[data-drop]');
    root.querySelectorAll('.drop-target').forEach((x) => x !== target && x.classList.remove('drop-target'));
    if (!kind || !target || !accepts(target, kind)) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = kind === 'items' && target.dataset.drop === 'tag' ? 'copy' : 'move';
    target.classList.add('drop-target');
    // hovering a collapsed folder for a moment opens it
    if (target.dataset.folder && !state.openFolders.has(Number(target.dataset.folder))) {
      clearTimeout(target._openTimer);
      target._openTimer = setTimeout(() => { state.openFolders.add(Number(target.dataset.folder)); saveOpenFolders(); paintFolders(); }, 800);
    }
  });
  root.addEventListener('dragleave', (e) => {
    const target = e.target.closest('[data-drop]');
    if (target && !target.contains(e.relatedTarget)) { target.classList.remove('drop-target'); clearTimeout(target._openTimer); }
  });
  root.addEventListener('drop', (e) => {
    const kind = dragKind(e);
    const target = e.target.closest('[data-drop]');
    if (!kind || !target || !accepts(target, kind)) return;
    e.preventDefault();
    e.stopPropagation();
    target.classList.remove('drop-target');
    const d = target.dataset.drop;
    if (kind === 'items') {
      const ids = JSON.parse(e.dataTransfer.getData(ITEMS_MIME) || '[]');
      if (d === 'folder') A.moveToFolder(ids, Number(target.dataset.folder));
      if (d === 'unfiled') A.moveToFolder(ids, null);
      if (d === 'tag') { const t = tagById(target.dataset.tag); if (t) A.addTags(ids, [t.name]); }
      if (d === 'trash') { A.select(ids); A.trashSelected(); }
    } else if (kind === 'folder') {
      const id = Number(e.dataTransfer.getData(FOLDER_MIME));
      const parent = d === 'folder-root' ? null : Number(target.dataset.folder);
      if (id && id !== parent) A.moveFolder(id, parent);
    } else if (kind === 'files') {
      A.setView(`folder:${target.dataset.folder}`);
      A.addPdfsAsItems(e.dataTransfer.files);
    }
  });
}
