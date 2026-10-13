// Right-hand item pane: editable metadata, authors, abstract, attachments.
import * as A from '../actions.js';
import { api, openAttachment } from '../api.js';
import { icon, typeIcon } from '../icons.js';
import { byId, emit, folderById, folderPath, itemTypeLabel, on, selectedItems, state } from '../store.js';
import { openMenu } from '../ui/menu.js';
import { toast, toastError } from '../ui/toast.js';
import { copyText, debounce, escapeHtml, formatDate, plural, shortAuthors, storage, store } from '../util.js';
import { openExport } from './exportDialog.js';
import { copyKeys, deleteAttachment, pickFiles, renameAttachment } from './itemlist.js';
import { openMetadataReview } from './metadataReview.js';
import { confirmKeyChange } from './notes/rekeyDialog.js';
import { fetchReferencesBulk, fillReferences } from './referencesSection.js';
import { addSelectionToFolder, editTagsDialog, tagInput } from './organizeDialogs.js';

let root, shownId = null;
// Sections open/closed: the user's choice per section, else the default (Abstract and
// References start collapsed; Notes, Info, Folder & Tags and Attachments open).
const DEFAULT_COLLAPSED = new Set(['abstract', 'references']);
const sectionState = storage('zc.sectionState', null)
  || Object.fromEntries(storage('zc.collapsedSections', []).map((id) => [id, true]));   // older setting
const isCollapsed = (id) => (id in sectionState ? sectionState[id] : DEFAULT_COLLAPSED.has(id));
let showAllFields = storage('zc.showAllFields', false);

export function initItemPane() {
  root = document.getElementById('item-pane');
  root.addEventListener('click', handleClick);
  root.addEventListener('dragover', (e) => { if (shownId && [...e.dataTransfer.types].includes('Files')) e.preventDefault(); });
  root.addEventListener('drop', (e) => {
    if (shownId && e.dataTransfer.files.length && !e.defaultPrevented) { e.preventDefault(); A.uploadAttachments(shownId, e.dataTransfer.files); }
  });
  on('selection', render);
  on('items', render);
  on('item-updated', (id) => { if (id === shownId) refreshHeader(); });
  on('focus-field', ({ id, field }) => {
    if (shownId !== id) render(true);
    setTimeout(() => {
      const el = root.querySelector(`[data-field="${field}"]`);
      if (el) { el.focus(); selectAll(el); }
    }, 30);
  });
  render();
}

function selectAll(el) {
  if (el.isContentEditable) {
    const r = document.createRange(); r.selectNodeContents(el);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  } else el.select?.();
}

function render(force = false) {
  const items = selectedItems();
  if (items.length === 1) {
    const it = items[0];
    // don't clobber a field the user is typing in
    if (!force && it.id === shownId && root.contains(document.activeElement) && document.activeElement !== document.body) return;
    const cached = it._lean && whole.get(it.id);
    if (cached && cached.date_modified === it.date_modified) {   // fetched before and unchanged since
      const i = state.items.indexOf(it);
      if (i >= 0) state.items[i] = { ...cached };
      renderItem(state.items[i] || cached);
      return;
    }
    renderItem(it);
    if (it._lean) loadWhole(it.id);
  } else if (items.length > 1) {
    shownId = null;
    renderMulti(items);
  } else {
    shownId = null;
    renderEmpty();
  }
}

/** The list holds lean items (no abstract, no empty fields): fetch the whole one for the pane. */
const loading = new Set();
const whole = new Map();   // id → last whole item fetched (the list reloads lean copies)
async function loadWhole(id) {
  if (loading.has(id)) return;
  loading.add(id);
  try {
    const full = await api.getItem(id);
    if (whole.size > 200) whole.clear();
    whole.set(id, full);
    const i = state.items.findIndex((x) => x.id === id);
    if (i < 0 || !state.items[i]._lean) return;   // edited meanwhile: the store already has a newer whole item
    // replace (not merge): fields the lean copy left out because they were empty stay empty
    state.items[i] = { ...full };
    if (shownId !== id) return;
    const typing = root.contains(document.activeElement) && document.activeElement !== document.body;
    if (!typing) renderItem(state.items[i]);
    else {   // only fill in the abstract, leave the field being typed in alone
      const slot = root.querySelector('[data-abstract-loading]');
      if (slot) slot.outerHTML = fv('abstract', full.abstract, { multiline: true, placeholder: 'Add an abstract…' });
    }
  } catch { /* the pane keeps the lean copy */ } finally { loading.delete(id); }
}

function renderEmpty() {
  const n = state.counts[state.view] || 0;
  const where = { trash: 'in the Trash', duplicates: 'look like duplicates' }[state.view] || 'in your library';
  const tip = state.view === 'duplicates' && n ? '<p class="subtle">Select one item of a group and choose “Keep This One” to trash the others.</p>' : '';
  root.innerHTML = `<div class="empty-state">${icon(state.view === 'trash' ? 'trash' : 'doc')}
    <h3>${state.view === 'trash' ? 'Trash' : 'No item selected'}</h3>
    <p class="muted">${plural(n, 'item')} ${where}.</p>${tip}</div>`;
}

function renderMulti(items) {
  const ids = items.map((i) => i.id);
  const trash = state.view === 'trash';
  root.innerHTML = `<div class="multi"><div class="count">${items.length}</div><div class="muted">items selected</div>
    <div class="actions">
      ${trash ? `<button class="btn" data-a="restore">${icon('restore')} Restore to Library</button>
                 <button class="btn btn-danger" data-a="delete">${icon('trash')} Delete Permanently</button>`
    : `<button class="btn" data-a="move">${icon('folderPlus')} Add to Folder…</button>
       <button class="btn" data-a="tags">${icon('tag')} Tags…</button>
       <button class="btn" data-a="metadata">${icon('sparkle')} Retrieve Metadata…</button>
       <button class="btn" data-a="find-pdfs">${icon('find')} Find PDFs${(() => { const n = items.filter((i) => !(i.attachments || []).length).length; return n && n < items.length ? ` (${n} missing)` : ''; })()}</button>
       <button class="btn" data-a="references">${icon('link')} Fetch References</button>
       <button class="btn" data-a="export">${icon('export')} Export…</button>
       <button class="btn" data-a="keys">${icon('key')} Copy Citation Keys</button>
       <button class="btn" data-a="regen">${icon('refresh')} Regenerate Keys</button>
       <button class="btn btn-danger" data-a="trash">${icon('trash')} Move to Trash</button>`}
    </div></div>`;
  root.querySelector('.actions').addEventListener('click', (e) => {
    const a = e.target.closest('[data-a]')?.dataset.a;
    if (a === 'move') addSelectionToFolder(ids);
    if (a === 'tags') editTagsDialog(ids);
    if (a === 'metadata') openMetadataReview(ids);
    if (a === 'find-pdfs') A.findPdfs(ids);
    if (a === 'references') fetchReferencesBulk(ids);
    if (a === 'export') openExport(ids);
    if (a === 'keys') copyKeys(items);
    if (a === 'regen') A.regenerateCitekeys(ids);
    if (a === 'trash') A.trashSelected();
    if (a === 'restore') A.restoreSelected();
    if (a === 'delete') A.deleteForever(ids);
  });
}

/* ------------------------------------------------------------ one item */
function fieldLabel(key, type) {
  const s = state.schema;
  if (key === 'journal') return s.containerLabels[type] || 'Publication';
  return s.fieldLabelOverrides[type]?.[key] || s.fields.find((f) => f.key === key)?.label || key;
}

// Info: the citation key first, then the fields that identify the work; everything else
// (type-specific details, URL, Extra, rare identifiers…) is behind "Show more fields".
const MAIN_FIELDS = ['year', 'journal', 'doi'];

function infoFieldKeys(it) {
  const s = state.schema;
  const type = it.item_type || 'journalArticle';
  const typeFields = s.typeFields[type] || [];
  const main = MAIN_FIELDS.filter((k) => k !== 'journal' || typeFields.includes('journal'));
  const shown = new Set(['citation_key', 'title', 'authors', 'item_type', 'abstract', ...main]);
  const more = [];
  for (const k of [...typeFields, ...s.commonFields, ...s.fields.filter((f) => f.editable).map((f) => f.key)]) {
    if (!shown.has(k) && !more.includes(k)) more.push(k);
  }
  return { main, more };
}

function fv(key, value, { multiline = false, placeholder = '' } = {}) {
  const v = value === null || value === undefined ? '' : String(value);
  return `<div class="fv" contenteditable="plaintext-only" spellcheck="${multiline ? 'true' : 'false'}" data-field="${key}"
    data-multiline="${multiline}" data-empty="${v === ''}" data-placeholder="${escapeHtml(placeholder)}">${escapeHtml(v)}</div>`;
}

function section(id, title, bodyHtml, { count = '', act = '' } = {}) {
  return `<div class="section ${isCollapsed(id) ? 'collapsed' : ''}" data-section="${id}">
    <button class="section-head">${icon('chevronDown', 'icon-sm chev')}${title}${count !== '' ? ` <span class="count">${count}</span>` : ''}${act ? `<span class="act">${act}</span>` : ''}</button>
    <div class="section-body">${bodyHtml}</div></div>`;
}

function renderItem(it) {
  shownId = it.id;
  const type = it.item_type || 'journalArticle';
  const trash = state.view === 'trash';
  const { main, more } = infoFieldKeys(it);
  const typeOptions = state.schema.itemTypes.map((t) => `<option value="${t.key}" ${t.key === type ? 'selected' : ''}>${escapeHtml(t.label)}</option>`).join('');

  const fieldRow = (key) => {
    let tools = '';
    if (key === 'doi' && it.doi) tools = `<button class="icon-btn" data-tool="open-doi" title="Open DOI">${icon('open', 'icon-sm')}</button>`;
    if (key === 'url' && it.url) tools = `<button class="icon-btn" data-tool="open-url" title="Open URL">${icon('open', 'icon-sm')}</button>`;
    if (key === 'citation_key') tools = `<button class="icon-btn" data-tool="copy-key" title="Copy">${icon('copy', 'icon-sm')}</button><button class="icon-btn" data-tool="regen-key" title="Regenerate from pattern">${icon('refresh', 'icon-sm')}</button>`;
    const multiline = ['extra'].includes(key);
    return `<div class="field-row"><label title="${escapeHtml(fieldLabel(key, type))}">${escapeHtml(fieldLabel(key, type))}</label>
      <div style="display:flex;align-items:start;gap:2px;min-width:0">${fv(key, it[key], { multiline, placeholder: '' })}${tools ? `<span class="field-tools">${tools}</span>` : ''}</div></div>`;
  };

  const authors = (it.authors || '').split(';').map((a) => a.trim()).filter(Boolean);
  const authorRows = (authors.length ? authors : ['']).map((a) => authorRow(a)).join('');

  const info = `
    ${fieldRow('citation_key')}
    <div class="field-row"><label>Title</label>${fv('title', it.title, { multiline: true, placeholder: 'Untitled' })}</div>
    <div class="field-row"><label>Authors</label><div class="authors">${authorRows}</div></div>
    ${main.map(fieldRow).join('')}
    ${showAllFields ? `
    <div class="field-row"><label>Item Type</label><select class="fv" data-field="item_type">${typeOptions}</select></div>
    ${more.map(fieldRow).join('')}
    <div class="field-row"><label>Date Added</label><div class="fv readonly">${escapeHtml(formatDate(it.date_added, true))}</div></div>
    <div class="field-row"><label>Modified</label><div class="fv readonly">${escapeHtml(formatDate(it.date_modified, true))}</div></div>` : ''}
    <button class="more-toggle" data-tool="more">${showAllFields ? 'Show fewer fields' : `Show ${more.length + 1} more fields`}</button>`;

  const atts = it.attachments || [];
  const attHtml = `<div class="att-list">${atts.map((a) => `
      <div class="att" data-att="${a.id}" title="Open ${escapeHtml(a.name)}">${icon('pdf')}
        <div style="flex:1;min-width:0"><div class="att-name">${escapeHtml(a.name || 'Attachment')}</div>
        <div class="att-meta">PDF · added ${escapeHtml(formatDate(a.date_added))}</div></div>
        <button class="icon-btn" data-att-menu="${a.id}" title="More">${icon('drag', 'icon-sm')}</button></div>`).join('')}</div>
    ${trash ? '' : `<div class="att-drop" data-tool="add-pdf">${icon('paperclip', 'icon-sm')} Drop a PDF here or click to attach</div>`}`;

  const snippet = state.hits[it.id]?.fulltext;
  root.innerHTML = `
    <div class="ip-header">
      <div class="ip-type">${typeIcon(type, 'icon-sm')} <span data-h="type">${escapeHtml(itemTypeLabel(type))}</span>${trash ? ' <span class="badge warn">In Trash</span>' : ''}</div>
      <h2 class="ip-title" data-h="title">${escapeHtml(it.title || 'Untitled')}</h2>
      <div class="ip-sub" data-h="sub">${escapeHtml(subline(it))}</div>
      ${state.view === 'duplicates' ? `<div class="ip-actions"><button class="btn btn-sm btn-primary" data-tool="keep-only">${icon('check', 'icon-sm')} Keep This One, Trash the Others</button></div>` : ''}
      <div class="ip-actions">
        ${trash ? `<button class="btn btn-sm" data-tool="restore">${icon('restore', 'icon-sm')} Restore</button>`
    : `${atts.length ? `<button class="btn btn-sm btn-primary" data-tool="open-pdf">${icon('pdf', 'icon-sm')} Open PDF</button>`
    : `<button class="btn btn-sm" data-tool="find-pdf" title="Look for an open-access PDF online">${icon('find', 'icon-sm')} Find PDF</button>`}
           <button class="btn btn-sm" data-tool="metadata" title="Fetch metadata from Crossref/arXiv and review changes">${icon('sparkle', 'icon-sm')} Retrieve Metadata</button>
           <button class="icon-btn" data-tool="more-menu" title="More actions">${icon('drag', 'icon-sm')}</button>`}
      </div>
    </div>
    <div class="ip-body">
      ${snippet && state.query ? section('fulltext', 'Full-text match', `<div class="ft-snippet">${snippet}</div>`) : ''}
      ${section('info', 'Info', info)}
      ${section('abstract', 'Abstract', it._lean   // the list has no abstracts: shown once the whole item is here (not editable before, so it is never overwritten)
    ? '<div class="muted" data-abstract-loading>Loading…</div>'
    : fv('abstract', it.abstract, { multiline: true, placeholder: 'Add an abstract…' }))}
      ${state.settings.notes_enabled !== false ? section('notes', 'Notes', '<div class="ip-notes" data-ip-notes><span class="muted">Loading…</span></div>') : ''}
      ${trash ? '' : section('organize', 'Folder &amp; Tags', `
        <div class="field-row"><label>${(it.folder_ids || []).length > 1 ? 'Folders' : 'Folder'}</label>
          <div class="folder-chips">${(it.folder_ids || []).filter((f) => folderById(f)).map((f) => `
            <span class="folder-chip" data-open-folder="${f}" title="Open this folder">${icon('folder', 'icon-sm')}<span>${escapeHtml(folderPath(f).join(' / '))}</span>
              <button class="x" data-remove-folder="${f}" title="Remove from this folder (the item stays in your library)">${icon('close', 'icon-sm')}</button></span>`).join('')}
            <button class="folder-add" data-tool="pick-folder" title="Add to a folder — an item can be in several">${icon('plus', 'icon-sm')} ${(it.folder_ids || []).length ? 'Add' : 'Add to folder'}</button>
          </div></div>
        <div class="field-row"><label>Tags</label><div class="ip-tags"></div></div>`, { count: (it.tags || []).length || '' })}
      ${section('references', 'References', '<div class="ip-refs" data-ip-refs><span class="muted">Loading…</span></div>')}
      ${section('attachments', 'Attachments', attHtml, { count: atts.length })}
    </div>`;
  bindItem(it);
  // Notes and references take a request each: wait until the selection rests, so holding an
  // arrow key through a big library does not queue hundreds of them (issue #24).
  clearTimeout(fillTimer);
  const fill = () => {
    if (shownId !== it.id) return;
    fillNotes(it);
    fillReferences(root.querySelector('[data-ip-refs]'), it, () => shownId === it.id);
  };
  if (Date.now() - lastFill > FILL_SETTLE_MS) { lastFill = Date.now(); fill(); }
  else fillTimer = setTimeout(() => { lastFill = Date.now(); fill(); }, FILL_SETTLE_MS);
}
const FILL_SETTLE_MS = 180;
let fillTimer = null, lastFill = 0;

/* ---------------------------------------------------------------- notes */
async function fillNotes(it) {
  const el = root.querySelector('[data-ip-notes]');
  if (!el) return;
  let res;
  try { res = await api.itemNotes(it.id); } catch (e) { el.innerHTML = `<span class="muted">${escapeHtml(e.message)}</span>`; return; }
  if (shownId !== it.id || !el.isConnected) return;
  if (!res.key) { el.innerHTML = '<span class="muted">Give this item a citation key to write notes about it.</span>'; return; }
  const trash = state.view === 'trash';
  const paper = res.paper_notes[0];
  const byNote = new Map();
  for (const c of res.citing) {
    if (paper && c.path === paper.path) continue;           // its own paper note is shown above
    (byNote.get(c.path) || byNote.set(c.path, { title: c.source_title, path: c.path, refs: [] }).get(c.path)).refs.push(c);
  }
  const head = paper
    ? `<button class="ip-note paper" data-open-note="${escapeHtml(paper.path)}">${icon('edit', 'icon-sm')}<span class="nm">${escapeHtml(paper.title || 'Paper note')}</span><span class="muted">paper note</span></button>`
    : (trash ? '' : `<button class="btn btn-sm" data-tool="new-paper-note">${icon('plus', 'icon-sm')} Create paper note</button>`);
  const list = [...byNote.values()].map((n) => `
    <div class="ip-cited">
      <button class="ip-note" data-open-note="${escapeHtml(n.path)}" data-open-line="${n.refs[0].line}">${icon('doc', 'icon-sm')}<span class="nm">${escapeHtml(n.title || n.path)}</span>
        ${n.refs.length > 1 ? `<span class="muted">×${n.refs.length}</span>` : ''}</button>
      ${n.refs.slice(0, 3).map((r) => `<button class="ip-quote" data-open-note="${escapeHtml(n.path)}" data-open-line="${r.line}">${escapeHtml(r.context)}</button>`).join('')}
    </div>`).join('');
  el.innerHTML = `${head}${list ? `<div class="ip-cited-label">Cited in</div>${list}` : (paper ? '' : '<div class="muted ip-none">No note cites this item yet.</div>')}`;
  const count = root.querySelector('[data-section="notes"] .section-head');
  const n = (paper ? 1 : 0) + byNote.size;
  if (count && n && !count.querySelector('.count')) count.insertAdjacentHTML('beforeend', ` <span class="count">${n}</span>`);
}

function subline(it) {
  return [shortAuthors(it.authors), it.year, it.journal].filter(Boolean).join(' · ');
}

function refreshHeader() {
  const it = byId(shownId);
  if (!it) return;
  const t = root.querySelector('[data-h="title"]');
  if (t) t.textContent = it.title || 'Untitled';
  const s = root.querySelector('[data-h="sub"]');
  if (s) s.textContent = subline(it);
  // reflect server-side changes (e.g. an auto-generated citation key) in fields not being edited
  root.querySelectorAll('.fv[contenteditable][data-field]').forEach((el) => {
    if (el === document.activeElement) return;
    const v = it[el.dataset.field] == null ? '' : String(it[el.dataset.field]);
    if (el.textContent !== v) { el.textContent = v; el.dataset.empty = v === '' ? 'true' : 'false'; }
  });
}

function authorRow(a) {
  const [last, first] = a.includes(',') ? a.split(/,(.*)/s).map((x) => (x || '').trim()) : [a, ''];
  return `<div class="author-row">
    <input class="last" placeholder="Last" value="${escapeHtml(last)}" aria-label="Last name">
    <input class="first" placeholder="First" value="${escapeHtml(first)}" aria-label="First name">
    <button class="icon-btn" data-author="remove" title="Remove author" tabindex="-1">${icon('minus', 'icon-sm')}</button>
    <button class="icon-btn" data-author="add" title="Add author below" tabindex="-1">${icon('plus', 'icon-sm')}</button></div>`;
}

function bindItem(it) {
  const id = it.id;

  // tags: changes are saved immediately
  const tagBox = root.querySelector('.ip-tags');
  if (tagBox) {
    tagInput(tagBox, {
      tags: it.tags || [],
      placeholder: (it.tags || []).length ? 'Add…' : 'Add a tag…',
      onAdd: (name) => api.tagItems([id], [name], []).then(() => A.refreshItems([id])).catch(toastError),
      onRemove: (name) => api.tagItems([id], [], [name]).then(() => A.refreshItems([id])).catch(toastError),
    });
  }

  // sections collapse
  root.querySelectorAll('.section-head').forEach((b) => b.addEventListener('click', (e) => {
    if (e.target.closest('.act')) return;
    const sec = b.parentElement;
    sectionState[sec.dataset.section] = sec.classList.toggle('collapsed');
    store('zc.sectionState', sectionState);
  }));

  // text fields
  root.querySelectorAll('.fv[contenteditable]').forEach((el) => {
    if (!('contentEditable' in el) || el.contentEditable !== 'plaintext-only') el.contentEditable = 'true'; // Firefox
    let original = el.textContent;
    el.addEventListener('focus', () => { original = el.textContent; });
    el.addEventListener('input', () => { el.dataset.empty = el.textContent === '' ? 'true' : 'false'; });
    el.addEventListener('paste', (e) => {
      e.preventDefault();
      const text = (e.clipboardData.getData('text/plain') || '').replace(el.dataset.multiline === 'true' && el.dataset.field !== 'title' ? /\r/g : /\s*\n\s*/g, ' ');
      document.execCommand('insertText', false, text);
    });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { el.textContent = original; el.blur(); e.stopPropagation(); }
      if (e.key === 'Enter' && (el.dataset.multiline !== 'true' || el.dataset.field === 'title') && !e.shiftKey) { e.preventDefault(); el.blur(); }
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); el.blur(); }
    });
    el.addEventListener('blur', async () => {
      const value = el.dataset.field === 'title' ? el.textContent.replace(/\s*\n\s*/g, ' ').trim() : el.textContent.trim();
      if (value === (original || '').trim()) return;
      if (el.dataset.field === 'citation_key' && state.settings.notes_enabled !== false
          && !(await confirmKeyChange((original || '').trim(), value))) {
        el.textContent = original;
        el.dataset.empty = original ? 'false' : 'true';
        return;
      }
      original = value;
      try {
        await A.updateField(id, el.dataset.field, value);
        el.classList.remove('saved'); void el.offsetWidth; el.classList.add('saved');
      } catch { el.textContent = original; }
    });
  });

  root.querySelector('select[data-field="item_type"]')?.addEventListener('change', async (e) => {
    await A.updateField(id, 'item_type', e.target.value);
    render(true);
  });

  // authors
  const box = root.querySelector('.authors');
  const saveAuthors = debounce(async () => {
    const value = [...box.querySelectorAll('.author-row')].map((r) => {
      const l = r.querySelector('.last').value.trim(), f = r.querySelector('.first').value.trim();
      return l || f ? (f ? `${l}, ${f}` : l) : '';
    }).filter(Boolean).join('; ');
    if (value === (byId(id)?.authors || '')) return;
    await A.updateField(id, 'authors', value);
  }, 700);
  box.addEventListener('input', saveAuthors);
  box.addEventListener('focusout', (e) => { if (!box.contains(e.relatedTarget)) saveAuthors.flush(); });
  box.addEventListener('click', (e) => {
    const b = e.target.closest('[data-author]');
    if (!b) return;
    const row = b.closest('.author-row');
    if (b.dataset.author === 'add') {
      const n = document.createElement('div');
      n.innerHTML = authorRow('');
      row.after(n.firstElementChild);
      row.nextElementSibling.querySelector('.last').focus();
    } else {
      if (box.children.length > 1) row.remove();
      else row.querySelectorAll('input').forEach((i) => { i.value = ''; });
      saveAuthors.flush();
    }
  });
  box.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
      e.preventDefault();
      const row = e.target.closest('.author-row');
      if (e.shiftKey || e.target.classList.contains('first')) {
        row.querySelector('[data-author="add"]').click();
      } else row.querySelector('.first').focus();
    }
    if (e.key === 'Escape') e.target.blur();
  });

  // attachment drop zone
  const dz = root.querySelector('.att-drop');
  if (dz) {
    dz.addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); dz.classList.add('over'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('over'));
    dz.addEventListener('drop', (e) => { e.preventDefault(); e.stopPropagation(); dz.classList.remove('over'); A.uploadAttachments(id, e.dataTransfer.files); });
  }
}

async function handleClick(e) {
  const id = shownId;
  if (!id) return;
  const it = byId(id);
  if (!it) return;
  const rm = e.target.closest('[data-remove-folder]');
  if (rm) { e.stopPropagation(); A.removeFolderMembership(id, Number(rm.dataset.removeFolder)); return; }
  const open = e.target.closest('[data-open-folder]');
  if (open) { A.setView(`folder:${open.dataset.openFolder}`); return; }
  const on = e.target.closest('[data-open-note]');
  if (on) { emit('open-note', { path: on.dataset.openNote, line: Number(on.dataset.openLine) || null }); return; }
  const t = e.target.closest('[data-tool]')?.dataset.tool;
  const it2 = byId(id) || it;
  if (t === 'new-paper-note') {
    try {
      const res = await api.createNote({ item_id: id });
      emit('open-note', { path: res.path });
    } catch (err) {
      if (err.data?.path) emit('open-note', { path: err.data.path }); else toastError(err);
    }
    return;
  }
  if (!t) {
    const att = e.target.closest('.att');
    const menuBtn = e.target.closest('[data-att-menu]');
    if (menuBtn) {
      const a = it2.attachments.find((x) => String(x.id) === menuBtn.dataset.attMenu);
      openMenu([
        { label: 'Open', icon: 'open', run: () => openAttachment(it2, a) },
        { label: 'Rename…', icon: 'edit', run: () => renameAttachment(a) },
        'sep',
        { label: 'Delete Attachment', icon: 'trash', danger: true, run: () => deleteAttachment(a) },
      ], menuBtn);
    } else if (att) {
      const a = it2.attachments.find((x) => String(x.id) === att.dataset.att);
      if (a) openAttachment(it2, a);
    }
    return;
  }
  if (t === 'open-pdf') {
    const atts = it2.attachments || [];
    if (atts.length > 1) openMenu(atts.map((a) => ({ label: `Open “${a.name || 'PDF'}”`, icon: 'pdf', run: () => openAttachment(it2, a) })), e.target.closest('[data-tool]'));
    else A.openPdf(it2);
  }
  if (t === 'pick-folder') addSelectionToFolder([id]);
  if (t === 'keep-only') A.keepOnly(id);
  if (t === 'metadata') openMetadataReview(id);
  if (t === 'find-pdf') A.findPdf(id);
  if (t === 'restore') A.restoreSelected();
  if (t === 'open-doi') window.open(`https://doi.org/${it2.doi}`, '_blank');
  if (t === 'open-url') window.open(it2.url, '_blank');
  if (t === 'copy-key') { if (it2.citation_key) { await copyText(it2.citation_key); toast(`Copied ${it2.citation_key}`, { type: 'success' }); } }
  if (t === 'regen-key') { await A.regenerateCitekeys([id]); render(true); }
  if (t === 'add-pdf') pickFiles((files) => A.uploadAttachments(id, files));
  if (t === 'more') { showAllFields = !showAllFields; store('zc.showAllFields', showAllFields); render(true); }
  if (t === 'more-menu') {
    openMenu([
      { label: 'Find Available PDF', icon: 'find', run: () => A.findPdf(id) },
      { label: 'Add PDF Attachment…', icon: 'paperclip', run: () => pickFiles((files) => A.uploadAttachments(id, files)) },
      it2.doi || it2.url ? { label: 'View Online', icon: 'globe', run: () => window.open(it2.url || `https://doi.org/${it2.doi}`, '_blank') } : null,
      'sep',
      { label: 'Copy Citation Key', icon: 'key', run: () => copyKeys([it2]) },
      { label: 'Export Item…', icon: 'export', run: () => openExport([id]) },
      'sep',
      { label: 'Move to Trash', icon: 'trash', danger: true, run: A.trashSelected },
    ], e.target.closest('[data-tool]'));
  }
}

export function toggleItemPane(force) {
  const hide = force !== undefined ? !force : !root.classList.contains('collapsed');
  root.classList.toggle('collapsed', hide);
  document.getElementById('resizer-right').hidden = hide;
  store('zc.itemPaneHidden', hide);
  emit('layout');
}
