// The items table: columns (sort, resize, reorder, show/hide), rows,
// selection, keyboard navigation, context menus and file drops.
import * as A from '../actions.js';
import { api, openAttachment } from '../api.js';
import { icon, typeIcon } from '../icons.js';
import { byId, COLUMNS, emit, itemTypeLabel, on, saveColumns, saveSort, selectedItems, sortedItems, state } from '../store.js';
import { promptDialog } from '../ui/dialog.js';
import { closeMenu, openMenu } from '../ui/menu.js';
import { toast, toastError } from '../ui/toast.js';
import { copyText, escapeHtml, formatDate, modKey, modLabel, plural, shortAuthors } from '../util.js';
import { openExport } from './exportDialog.js';
import { openMetadataReview } from './metadataReview.js';

const COLDEF = Object.fromEntries(COLUMNS.map((c) => [c.id, c]));
let pane, scroller, table, head, body;
let order = [];              // ids in display order

export function initItemList() {
  pane = document.getElementById('items-pane');
  pane.innerHTML = `<div class="items-scroll" tabindex="0" aria-label="Items">
      <div class="items-table"><div class="items-head" role="row"></div><div class="items-body" role="rowgroup"></div></div>
    </div>`;
  scroller = pane.querySelector('.items-scroll');
  table = pane.querySelector('.items-table');
  head = pane.querySelector('.items-head');
  body = pane.querySelector('.items-body');

  renderHead();
  bindHead();
  bindBody();
  bindKeyboard();
  bindDrops();

  on('items', renderBody);
  on('selection', paintSelection);
  on('item-updated', (id) => updateRow(id));
  on('columns', () => { renderHead(); renderBody(); });
  on('focus-list', () => scroller.focus());
}

/* ================================================================ columns */
function visibleCols() { return state.columns.filter((c) => c.visible); }

function applyGrid() {
  const tpl = visibleCols().map((c) => (c.id === 'title' ? `minmax(${c.width}px, 1fr)` : `${c.width}px`)).join(' ');
  table.style.setProperty('--cols', tpl);
  table.style.minWidth = `${visibleCols().reduce((n, c) => n + c.width, 0)}px`;
}

function renderHead() {
  const { col, dir } = state.sort;
  head.innerHTML = visibleCols().map((c) => {
    const def = COLDEF[c.id];
    const sorted = col === c.id;
    const label = c.id === 'attachments' ? icon('paperclip', 'icon-sm') : `<span class="label">${escapeHtml(def.label)}</span>`;
    return `<div class="head-cell ${sorted ? 'sorted' : ''}" data-col="${c.id}" draggable="${def.fixed ? 'false' : 'true'}" title="${escapeHtml(def.label)}">
      ${label}${sorted ? `<span class="sort">${dir === 'asc' ? '▲' : '▼'}</span>` : ''}
      ${def.fixed ? '' : '<span class="grip" data-grip></span>'}</div>`;
  }).join('');
  applyGrid();
}

function bindHead() {
  let dragId = null;
  head.addEventListener('click', (e) => {
    if (e.target.closest('[data-grip]')) return;
    const cell = e.target.closest('.head-cell');
    if (!cell) return;
    const id = cell.dataset.col;
    if (state.sort.col === id) state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
    else state.sort = { col: id, dir: ['date_added', 'date_modified', 'year'].includes(id) ? 'desc' : 'asc' };
    saveSort();
    renderHead();
    renderBody();
  });
  head.addEventListener('contextmenu', (e) => { e.preventDefault(); openColumnPicker({ x: e.clientX, y: e.clientY }); });

  // resize
  head.addEventListener('mousedown', (e) => {
    const grip = e.target.closest('[data-grip]');
    if (!grip) return;
    e.preventDefault(); e.stopPropagation();
    const id = grip.parentElement.dataset.col;
    const col = state.columns.find((c) => c.id === id);
    const startX = e.clientX, startW = col.width, min = COLDEF[id].min || 40;
    document.body.style.cursor = 'col-resize';
    const move = (ev) => { col.width = Math.max(min, Math.round(startW + ev.clientX - startX)); applyGrid(); };
    const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); document.body.style.cursor = ''; saveColumns(); };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
  head.addEventListener('dblclick', (e) => {
    const grip = e.target.closest('[data-grip]');
    if (!grip) return;
    autoFit(grip.parentElement.dataset.col);
  });

  // reorder
  head.addEventListener('dragstart', (e) => {
    const cell = e.target.closest('.head-cell');
    if (!cell || e.target.closest('[data-grip]')) { e.preventDefault(); return; }
    dragId = cell.dataset.col;
    cell.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/x-zc-column', dragId);
  });
  head.addEventListener('dragover', (e) => {
    if (!dragId) return;
    e.preventDefault();
    head.querySelectorAll('.drag-over').forEach((c) => c.classList.remove('drag-over'));
    e.target.closest('.head-cell')?.classList.add('drag-over');
  });
  head.addEventListener('drop', (e) => {
    if (!dragId) return;
    e.preventDefault();
    const target = e.target.closest('.head-cell')?.dataset.col;
    if (target && target !== dragId) {
      const from = state.columns.findIndex((c) => c.id === dragId);
      const [moved] = state.columns.splice(from, 1);
      const to = state.columns.findIndex((c) => c.id === target);
      state.columns.splice(to, 0, moved);
      saveColumns();
      emit('columns');
    }
  });
  head.addEventListener('dragend', () => { dragId = null; renderHead(); });
}

function autoFit(id) {
  const col = state.columns.find((c) => c.id === id);
  let w = 60;
  body.querySelectorAll(`.cell[data-col="${id}"]`).forEach((c) => { w = Math.max(w, c.scrollWidth + 4); });
  const hc = head.querySelector(`.head-cell[data-col="${id}"] .label`);
  if (hc) w = Math.max(w, hc.scrollWidth + 36);
  col.width = Math.min(w, 900);
  applyGrid();
  saveColumns();
}

export function openColumnPicker(at) {
  const entries = [{ section: 'Columns' }];
  for (const c of state.columns) {
    if (c.id === 'title') continue;
    entries.push({
      label: COLDEF[c.id].label, checked: c.visible, keepOpen: true,
      run: () => { c.visible = !c.visible; saveColumns(); emit('columns'); },
    });
  }
  entries.push('sep', {
    label: 'Reset Columns', icon: 'refresh', run: () => {
      localStorage.removeItem('zc.columns');
      const fresh = COLUMNS.map((c) => ({ id: c.id, width: c.width, visible: ['attachments', 'title', 'creator', 'year', 'journal', 'date_added'].includes(c.id) }));
      state.columns.splice(0, state.columns.length, ...fresh);
      saveColumns(); emit('columns');
    },
  });
  const m = openMenu(entries, at);
  m.style.maxHeight = '70vh'; m.style.overflow = 'auto';
}

/* =================================================================== rows */
function cellHtml(colId, it) {
  switch (colId) {
    case 'attachments': {
      const n = (it.attachments || []).length;
      return n ? `<span class="pdf-dot" data-act="open" title="Open PDF${n > 1 ? ` (${n} attachments)` : ''}">${icon('pdf', 'icon-sm')}</span>` : '';
    }
    case 'title': {
      const n = (it.attachments || []).length;
      const open = state.expanded.has(it.id);
      const hit = state.hits[it.id]?.fulltext ? ' <span class="badge accent" title="Matched in the full text">full text</span>' : '';
      return `<span class="twisty ${n ? '' : 'none'} ${open ? 'open' : ''}" data-act="toggle">${icon('chevron', 'icon-sm')}</span>`
        + `${typeIcon(it.item_type, 'icon-sm type-icon')}<span class="t">${escapeHtml(it.title || 'Untitled')}</span>${hit}`;
    }
    case 'creator': return escapeHtml(shortAuthors(it.authors));
    case 'item_type': return escapeHtml(itemTypeLabel(it.item_type));
    case 'date_added': case 'date_modified': return escapeHtml(formatDate(it[colId]));
    default: return escapeHtml(it[colId] ?? '');
  }
}

function rowHtml(it) {
  const cells = visibleCols().map((c) => {
    const title = c.id === 'creator' ? ` title="${escapeHtml(it.authors || '')}"` : (c.id === 'title' ? ` title="${escapeHtml(it.title || '')}"` : '');
    return `<div class="cell c-${c.id}" data-col="${c.id}"${title}>${cellHtml(c.id, it)}</div>`;
  }).join('');
  let html = `<div class="row ${state.selection.has(it.id) ? 'selected' : ''}" role="row" data-id="${it.id}">${cells}</div>`;
  if (state.expanded.has(it.id)) {
    for (const a of it.attachments || []) {
      html += `<div class="row child" data-parent="${it.id}" data-att="${a.id}"><div class="cell">${icon('pdf', 'icon-sm')}<span>${escapeHtml(a.name || 'Attachment')}</span></div></div>`;
    }
  }
  const snip = state.hits[it.id]?.fulltext;
  if (snip && state.query) html += `<div class="row snippet" data-parent="${it.id}"><div class="cell">${snip}</div></div>`;
  return html;
}

function renderBody() {
  const items = sortedItems();
  order = items.map((i) => i.id);
  pane.querySelector('.list-empty')?.remove();
  body.innerHTML = items.map(rowHtml).join('');
  painted = new Set([...state.selection].filter((id) => order.includes(id)));
  if (!items.length) pane.appendChild(emptyState());
  emit('list-rendered', items.length);
}

function updateRow(id) {
  const row = body.querySelector(`.row[data-id="${CSS.escape(id)}"]`);
  const it = byId(id);
  if (!row || !it) return;
  // remove old children/snippet, re-insert fresh html
  body.querySelectorAll(`[data-parent="${CSS.escape(id)}"]`).forEach((r) => r.remove());
  row.insertAdjacentHTML('afterend', rowHtml(it));
  row.remove();
}

function emptyState() {
  const el = document.createElement('div');
  el.className = 'list-empty';
  if (state.query) {
    el.innerHTML = `<div class="empty-state">${icon('search')}<h3>No results</h3>
      <p>Nothing matches “${escapeHtml(state.query)}” in <b>${escapeHtml(state.settingsMeta.searchModes?.[state.mode] || state.mode)}</b>.</p>
      <div class="actions">${state.mode !== 'fulltext' ? '<button class="btn" data-empty="fulltext">Search full text too</button>' : ''}
      <button class="btn btn-ghost" data-empty="clear">Clear search</button></div></div>`;
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
    const act = e.target.closest('[data-empty]')?.dataset.empty;
    if (act === 'fulltext') emit('set-search-mode', 'fulltext');
    if (act === 'clear') emit('clear-search');
    if (act === 'identifier') emit('open-identifier');
    if (act === 'import') emit('open-import');
    if (act === 'new') A.newItem();
  });
  return el;
}

let painted = new Set();
function paintSelection() {
  // only touch rows whose state changed (fast with thousands of rows)
  for (const id of painted) if (!state.selection.has(id)) rowEl(id)?.classList.remove('selected');
  for (const id of state.selection) if (!painted.has(id)) rowEl(id)?.classList.add('selected');
  painted = new Set(state.selection);
}
const rowEl = (id) => body.querySelector(`.row[data-id="${CSS.escape(id)}"]`);

function scrollToRow(id) {
  const row = body.querySelector(`.row[data-id="${CSS.escape(id)}"]`);
  if (!row) return;
  const top = row.offsetTop, bottom = top + row.offsetHeight;
  const headH = head.offsetHeight;
  if (top - headH < scroller.scrollTop) scroller.scrollTop = top - headH;
  else if (bottom > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = bottom - scroller.clientHeight;
}
on('selection', () => { if (state.cursor) scrollToRow(state.cursor); });

/* ============================================================ interaction */
function toggleExpand(id, force) {
  const open = force ?? !state.expanded.has(id);
  if (open) state.expanded.add(id); else state.expanded.delete(id);
  updateRow(id);
}

function bindBody() {
  body.addEventListener('mousedown', (e) => {
    const row = e.target.closest('.row');
    if (!row || e.button !== 0) return;
    if (row.dataset.att || row.classList.contains('snippet')) return;
    const id = row.dataset.id;
    if (e.target.closest('[data-act="toggle"]')) { toggleExpand(id); return; }
    if (e.shiftKey && state.anchor) {
      const a = order.indexOf(state.anchor), b = order.indexOf(id);
      const [s, t] = a < b ? [a, b] : [b, a];
      A.select(order.slice(s, t + 1), { cursor: id });
    } else if (modKey(e)) {
      const sel = new Set(state.selection);
      sel.has(id) ? sel.delete(id) : sel.add(id);
      A.select([...sel], { anchor: id, cursor: id });
    } else if (!state.selection.has(id) || state.selection.size > 1) {
      A.select([id], { anchor: id, cursor: id });
    }
  });
  body.addEventListener('click', (e) => {
    const child = e.target.closest('.row.child');
    if (child) {
      const it = byId(child.dataset.parent);
      const att = it?.attachments.find((a) => String(a.id) === child.dataset.att);
      if (att) openAttachment(it, att);
      return;
    }
    if (e.target.closest('[data-act="open"]')) A.openPdf(e.target.closest('.row').dataset.id);
    const snip = e.target.closest('.row.snippet');
    if (snip) A.openPdf(snip.dataset.parent);
  });
  body.addEventListener('dblclick', (e) => {
    const row = e.target.closest('.row[data-id]');
    if (!row || e.target.closest('[data-act]')) return;
    if (state.settings.open_pdf_on_double_click !== false) A.openPdf(row.dataset.id);
    else emit('focus-field', { id: row.dataset.id, field: 'title' });
  });
  body.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const child = e.target.closest('.row.child');
    if (child) return attachmentMenu(child.dataset.parent, child.dataset.att, { x: e.clientX, y: e.clientY });
    const row = e.target.closest('.row[data-id]');
    if (!row) return;
    if (!state.selection.has(row.dataset.id)) A.select([row.dataset.id], { anchor: row.dataset.id });
    itemMenu({ x: e.clientX, y: e.clientY });
  });
  scroller.addEventListener('mousedown', (e) => {
    if (e.target === scroller || e.target === body || e.target.closest('.list-empty')) {
      if (!e.target.closest('.list-empty button')) A.select([]);
    }
  });
}

export function itemMenu(at) {
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
  const hasPdf = one && (one.attachments || []).length > 0;
  const onlineUrl = one && (one.url || (one.doi ? `https://doi.org/${one.doi}` : ''));
  openMenu([
    one && { label: hasPdf ? 'Open PDF' : 'Open PDF (none attached)', icon: 'pdf', disabled: !hasPdf, run: () => A.openPdf(one), shortcut: '↵' },
    one && { label: 'View Online', icon: 'globe', disabled: !onlineUrl, run: () => window.open(onlineUrl, '_blank') },
    one && 'sep',
    one && { label: 'Retrieve Metadata…', icon: 'sparkle', run: () => openMetadataReview(one.id) },
    one && { label: 'Find Available PDF', icon: 'find', run: () => A.findPdf(one.id) },
    one && { label: 'Add PDF Attachment…', icon: 'paperclip', run: () => pickFiles((files) => A.uploadAttachments(one.id, files)) },
    one && 'sep',
    { label: one ? 'Copy Citation Key' : 'Copy Citation Keys', icon: 'key', run: () => copyKeys(items), shortcut: `${modLabel}⇧C` },
    { label: 'Copy as BibTeX', icon: 'copy', run: () => copyBibtex(ids) },
    { label: one ? 'Export Item…' : `Export ${items.length} Items…`, icon: 'export', run: () => openExport(ids) },
    { label: one ? 'Regenerate Citation Key' : 'Regenerate Citation Keys', icon: 'refresh', run: () => A.regenerateCitekeys(ids) },
    'sep',
    { label: one ? 'Move Item to Trash' : `Move ${items.length} Items to Trash`, icon: 'trash', danger: true, run: A.trashSelected, shortcut: '⌫' },
  ], at);
}

function attachmentMenu(itemId, attId, at) {
  const it = byId(itemId);
  const att = it?.attachments.find((a) => String(a.id) === String(attId));
  if (!att) return;
  openMenu([
    { label: 'Open', icon: 'open', run: () => openAttachment(it, att) },
    { label: 'Rename…', icon: 'edit', run: () => renameAttachment(att) },
    { label: 'Download', icon: 'download', run: () => { const a = document.createElement('a'); a.href = `/pdfs/${encodeURIComponent(att.url)}`; a.download = att.name || 'file.pdf'; a.click(); } },
    'sep',
    { label: 'Delete Attachment', icon: 'trash', danger: true, run: () => deleteAttachment(att) },
  ], at);
}

export async function renameAttachment(att) {
  const name = await promptDialog('Name', { title: 'Rename Attachment', value: att.name || '', confirm: 'Rename' });
  if (!name) return;
  try { await api.renameAttachment(att.id, name); await A.loadItems(); } catch (e) { toastError(e); }
}

export async function deleteAttachment(att) {
  const { confirmDialog } = await import('../ui/dialog.js');
  if (!(await confirmDialog(`Delete “${att.name}”?`, { title: 'Delete attachment', confirm: 'Delete', danger: true, detail: 'The file is removed from disk.' }))) return;
  try { await api.deleteAttachment(att.id); await A.loadItems(); toast('Attachment deleted'); } catch (e) { toastError(e); }
}

export async function copyKeys(items) {
  const keys = items.map((i) => i.citation_key).filter(Boolean);
  if (!keys.length) return toast('No citation keys yet — use “Regenerate Citation Key”.', { type: 'error' });
  await copyText(keys.join(', '));
  toast(`Copied ${keys.length === 1 ? keys[0] : plural(keys.length, 'key')}`, { type: 'success' });
}

async function copyBibtex(ids) {
  try {
    const res = await api.exportText('bibtex', ids);
    await copyText(res.text);
    toast(`Copied BibTeX for ${plural(res.count, 'item')}`, { type: 'success' });
  } catch (e) { toastError(e); }
}

export function pickFiles(cb, accept = 'application/pdf,.pdf', multiple = true) {
  const input = document.createElement('input');
  input.type = 'file'; input.accept = accept; input.multiple = multiple;
  input.onchange = () => { if (input.files.length) cb(input.files); };
  input.click();
}

/* ============================================================== keyboard */
function bindKeyboard() {
  scroller.addEventListener('keydown', (e) => {
    if (e.target !== scroller) return;
    const idx = order.indexOf(state.cursor);
    const move = (to, extend) => {
      to = Math.max(0, Math.min(order.length - 1, to));
      const id = order[to];
      if (!id) return;
      if (extend && state.anchor) {
        const a = order.indexOf(state.anchor);
        const [s, t] = a < to ? [a, to] : [to, a];
        A.select(order.slice(s, t + 1), { cursor: id });
      } else A.select([id], { anchor: id, cursor: id });
    };
    const page = Math.max(1, Math.floor(scroller.clientHeight / (body.querySelector('.row')?.offsetHeight || 28)) - 1);
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
      case 'Delete': case 'Backspace': if (state.selection.size) { e.preventDefault(); A.trashSelected(); } break;
      case 'a': case 'A': if (modKey(e)) { e.preventDefault(); A.select(order, { anchor: order[0], cursor: state.cursor }); } break;
      case 'c': case 'C': if (modKey(e) && e.shiftKey && state.selection.size) { e.preventDefault(); copyKeys(selectedItems()); } break;
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
function bindDrops() {
  let depth = 0, overRow = null;
  const isFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
  const clearRow = () => { overRow?.classList.remove('selected'); overRow = null; };
  pane.addEventListener('dragenter', (e) => { if (!isFiles(e)) return; depth++; pane.classList.add('drag-over'); });
  pane.addEventListener('dragleave', (e) => { if (!isFiles(e)) return; if (--depth <= 0) { depth = 0; pane.classList.remove('drag-over'); clearRow(); } });
  pane.addEventListener('dragover', (e) => {
    if (!isFiles(e)) return;
    e.preventDefault();
    const row = e.target.closest('.row[data-id]');
    if (row !== overRow) {
      clearRow();
      if (row && state.view === 'library') { overRow = row; row.classList.add('selected'); pane.classList.remove('drag-over'); } else pane.classList.add('drag-over');
    }
  });
  pane.addEventListener('drop', (e) => {
    if (!isFiles(e)) return;
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

export function focusList() { scroller?.focus(); closeMenu(); }
