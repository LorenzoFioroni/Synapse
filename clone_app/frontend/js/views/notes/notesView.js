// Notes workspace: file tree · editor · outline. Opened from "Notes" in the sidebar; it
// covers the item list and item pane (which keep their state underneath).
//
// Saving is handled by NoteSession (noteSession.js): autosave, conflict detection,
// offline retries and local drafts. This module only wires it to the UI.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { emit, on, state } from '../../store.js';
import { confirmDialog, openDialog, promptDialog } from '../../ui/dialog.js';
import { openMenu } from '../../ui/menu.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml, formatDate, h, modLabel, plural, storage, store } from '../../util.js';
import { compactDiff, lineDiff } from './diff.js';
import { createEditor, loadMD } from './editor.js';
import { splitFrontmatter } from './mdscan.js';
import { NoteSession } from './noteSession.js';
import { attachCiteCards, openCitation, showCard } from './citeCard.js';
import { shortAuthors } from './library.js';
import { createRenderer, slugHeading } from './render.js';
import { highlightCodeBlocks, renderEmbeds, renderMermaid, renderMermaidBlocks } from './richRender.js';

const NOTE_MIME = 'application/x-zc-note';

let root = null;          // #notes-view
let editor = null;        // editor handle (created once)
let session = null;       // NoteSession of the open note
let info = null;          // index info of the open note (title, headings, tags…)
let overview = { notes: [], folders: [] };
let collapsed = new Set(storage('zc.notes.collapsed', []));
let filter = '';
let opening = Promise.resolve();
let mode = storage('zc.notes.mode', 'live');           // 'live' | 'source' | 'read'
let editMode = mode === 'read' ? 'live' : mode;        // what Cmd+E returns to
let lib = null;                                        // markdown-it + KaTeX, once loaded
let renderer = null;                                   // for tables/math in the live preview
// What the backend says this note links to (refreshed after each save).
let linkData = { notes: new Map(), cites: new Map() };
let linkResp = null;                                   // last /notes/links answer (for the side panel)
// Citable library items (for autocomplete and citation cards), keyed by citation key.
let library = { list: [], byKey: new Map() };

const drafts = {
  get: (k) => storage(k, null),
  set: (k, v) => store(k, v),
  remove: (k) => { try { localStorage.removeItem(k); } catch { /* storage unavailable */ } },
};

const STATUS = {
  saved: ['Saved', 'ok'],
  dirty: ['Edited', ''],
  saving: ['Saving…', ''],
  offline: ['Not saved yet — retrying', 'warn'],
  conflict: ['Changed elsewhere', 'bad'],
};

export const notesOpen = () => document.body.dataset.workspace === 'notes';
export const notesEnabled = () => state.settings.notes_enabled !== false;

/* ================================================================== setup */
export function initNotes() {
  root = h(`<div id="notes-view" hidden>
    <section class="notes-tree-pane" aria-label="Notes">
      <div class="notes-tree-head">
        <span class="title">Notes</span>
        <button class="icon-btn" data-act="new-note" title="New note (${modLabel}N)">${icon('plus')}</button>
        <button class="icon-btn" data-act="new-folder" title="New folder">${icon('folderPlus')}</button>
        <button class="icon-btn" data-act="tree-menu" title="More">${icon('drag', 'icon-sm')}</button>
      </div>
      <div class="notes-filter"><input class="input" placeholder="Filter notes" spellcheck="false" aria-label="Filter notes"></div>
      <div class="notes-tree" role="tree" data-drop-folder=""></div>
    </section>
    <section class="notes-main">
      <div class="note-head">
        <div class="note-crumbs"></div>
        <span class="note-status" aria-live="polite"></span>
        <div class="segmented note-modes" role="group" aria-label="View">
          <button data-mode="live" title="Live preview: lines show rendered until you edit them">Live</button>
          <button data-mode="source" title="Plain Markdown">Source</button>
          <button data-mode="read" title="Read (${modLabel}E)">Read</button>
        </div>
      </div>
      <div class="note-body">
        <div class="note-editor"></div>
        <div class="note-read" tabindex="0" hidden><article class="prose"></article></div>
        <div class="note-empty empty-state">${icon('doc')}<h3>No note open</h3>
          <p>Pick a note on the left, or create one with <b>+</b> (${modLabel}N).</p></div>
      </div>
    </section>
    <aside class="notes-side" aria-label="Note details"></aside>
  </div>`);
  document.getElementById('app').appendChild(root);

  root.addEventListener('click', onClick);
  root.addEventListener('dblclick', onDblClick);
  window.zcNotes = { text: () => session?.text ?? null, path: () => session?.path ?? null };   // read-only, for tests
  root.querySelector('.note-read').addEventListener('click', onReadClick);
  attachCiteCards(root, { itemInfo, openNote: (p) => openNote(p), refreshLibrary: async () => { await loadLibrary(); await refreshTree(); } });
  let libTimer = null;
  on('items', () => { if (notesOpen()) { clearTimeout(libTimer); libTimer = setTimeout(loadLibrary, 500); } });
  root.querySelector('.note-read').addEventListener('dblclick', onReadDblClick);
  root.addEventListener('contextmenu', onContextMenu);
  root.querySelector('.notes-filter input').addEventListener('input', (e) => { filter = e.target.value.trim().toLowerCase(); paintTree(); });
  bindDnD();

  window.addEventListener('focus', () => { if (notesOpen()) { session?.checkRemote(); refreshTree(); } });
  document.addEventListener('visibilitychange', () => { if (document.hidden) session?.save(); });
  window.addEventListener('pagehide', flushOnUnload);
  // Diagrams are drawn for one theme: redraw them when light/dark changes.
  new MutationObserver(() => { if (!notesOpen() || !session) return; editor?.refresh(); if (mode === 'read') renderRead(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  on('search', () => { if (notesOpen()) closeNotes(); });   // typing in the library search shows the results
  on('settings', () => { if (!notesEnabled() && notesOpen()) closeNotes(); emit('notes-count', notesEnabled() ? overview.notes.length : null); });

  if (notesEnabled()) refreshTree().then(() => { if (storage('zc.notes.open', false)) openNotes(); });
}

/** Last chance when the tab closes: send unsaved text (the local draft stays as a safety net). */
function flushOnUnload() {
  if (!session || !session.dirty || session.status === 'conflict') return;
  try {
    fetch('/api/notes/note', {
      method: 'PUT', keepalive: true, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: session.path, text: session.text, hash: session.saved.hash }),
    });
  } catch { /* the draft is still there */ }
}

/* ============================================================ open/close */
export async function openNotes(path = null, line = null) {
  if (!notesEnabled()) return;
  document.body.dataset.workspace = 'notes';
  root.hidden = false;
  store('zc.notes.open', true);
  emit('workspace');
  await Promise.all([refreshTree(), loadLibrary()]);
  const target = path || session?.path || storage('zc.notes.active', null);
  if (target && overview.notes.some((n) => n.path === target)) {
    await openNote(target);
    if (line) gotoLine(line);
  } else if (!session) paintEmpty();
}

/** Show a 1-based line of the open note, in whichever mode is active. */
function gotoLine(line) {
  if (mode === 'read') {
    const el = [...root.querySelectorAll('.note-read [data-line]')].find((x) => Number(x.dataset.line) + 1 >= line);
    el?.scrollIntoView({ block: 'center' });
  } else editor?.gotoLine(line);
}

export async function closeNotes() {
  if (!notesOpen()) return;
  await session?.save();
  delete document.body.dataset.workspace;
  root.hidden = true;
  store('zc.notes.open', false);
  emit('workspace');
  emit('focus-list');
}

async function ensureEditor() {
  if (editor) return editor;
  lib = await loadMD();
  renderer = createRenderer(lib, { citeInfo, resolveNote });
  editor = await createEditor(root.querySelector('.note-editor'), {
    onChange: (text) => { session?.update(text); if (mode === 'read') scheduleRead(); },
    onSave: () => session?.save(),
    onBlur: () => session?.save(),
    onToggleRead: () => setMode(mode === 'read' ? editMode : 'read'),
    onImage: async (file) => {
      try { return (await api.uploadNoteImage(file)).markdown; } catch (e) { toastError(e); return null; }
    },
  }, {
    renderer: () => renderer,
    resolveNote,
    citeInfo,
    openNote: (target, heading) => followLink(target, heading),
    openCite: (key, page) => { if (!openCitation(key, page)) toast(`@${key} is not in your library.`); },
    mermaid: renderMermaid,
  }, mode === 'read' ? editMode : mode, {
    items: () => library.list,
    notes: () => overview.notes,
  });
  return editor;
}

/* ---------------------------------------------------- link data and modes */
const normTarget = (t) => t.trim().replace(/\\/g, '/').toLowerCase().replace(/\.md$/, '');

/** Path a [[target]] goes to: a path, null (no such note) or undefined (not known yet). */
function resolveNote(target) {
  const t = normTarget(target || '');
  if (!t) return session?.path;
  return linkData.notes.has(t) ? linkData.notes.get(t) : undefined;
}
/** Item info for a key: what the backend resolved for this note, else the library list. */
const citeInfo = (key) => linkData.cites.get(key) || library.byKey.get(key) || null;
/** Full info for cards (with PDF and paper note), from the library list. */
const itemInfo = (key) => library.byKey.get(key) || linkData.cites.get(key) || null;

async function loadLibrary() {
  try {
    const res = await api.citeIndex();
    library = { list: res.items, byKey: new Map(res.items.map((i) => [i.key, i])) };
    editor?.refresh();
  } catch { /* autocomplete stays empty until the next try */ }
}

async function loadLinks() {
  if (!session) return;
  const path = session.path;
  try {
    const res = await api.noteLinks(path);
    if (session?.path !== path) return;
    const notes = new Map(), cites = new Map();
    for (const l of res.outgoing) {
      if (l.kind === 'note' || l.kind === 'embed') notes.set(normTarget(l.target), l.resolved || null);
      if (l.kind === 'cite' && l.item) cites.set(l.target, l.item);
    }
    linkData = { notes, cites };
    linkResp = { ...res, path_for: path };
    editor?.refresh();
    paintSide();
    if (mode === 'read') renderRead();
  } catch { /* shown as unknown until the next save */ }
}

function setMode(m) {
  if (!['live', 'source', 'read'].includes(m)) m = 'live';
  mode = m;
  if (m !== 'read') editMode = m;
  store('zc.notes.mode', m);
  root.querySelectorAll('[data-mode]').forEach((b) => b.classList.toggle('active', b.dataset.mode === m));
  const read = root.querySelector('.note-read');
  root.classList.toggle('reading', m === 'read');
  read.hidden = m !== 'read';
  if (m === 'read') { renderRead(); read.focus(); return; }
  editor?.setMode(m);
  editor?.focus();
}

let readTimer = null;
function scheduleRead() { clearTimeout(readTimer); readTimer = setTimeout(renderRead, 300); }

/** Render the open note into the read view. */
function renderRead() {
  if (!session || !lib) return;
  const art = root.querySelector('.note-read .prose');
  const text = session.text.replace(/\r\n?/g, '\n');
  const fm = splitFrontmatter(text);
  const lineOffset = fm.bodyLine;
  const r = createRenderer(lib, { citeInfo, resolveNote, lineOffset });
  const props = fm.header === null ? '' : `<details class="properties"><summary>Properties</summary><dl>${fm.header.split('\n').filter((l) => l.trim())
    .map((l) => { const i = l.indexOf(':'); return i > 0 ? `<dt>${escapeHtml(l.slice(0, i))}</dt><dd>${escapeHtml(l.slice(i + 1).trim())}</dd>` : `<dd class="raw">${escapeHtml(l)}</dd>`; }).join('')}</dl></details>`;
  const scroll = art.parentElement.scrollTop;
  art.innerHTML = props + r.render(text.slice(fm.bodyStart));
  art.parentElement.scrollTop = scroll;
  highlightCodeBlocks(art);
  renderMermaidBlocks(art);
  renderEmbeds(art, new Set([session.path]));
}

async function followLink(target, heading) {
  const path = target ? resolveNote(target) : session?.path;
  if (path) {
    if (path !== session?.path) await openNote(path);
    if (heading) gotoHeading(heading);
    return;
  }
  if (!target) return;
  if (await confirmDialog(`There is no note called “${target}” yet.`, { title: 'Create note?', confirm: 'Create' })) {
    try {
      const res = await api.createNote({ title: target, folder: session ? parentOf(session.path) : '' });
      await refreshTree();
      await openNote(res.path);
    } catch (e) { toastError(e); }
  }
}

function gotoHeading(heading) {
  const want = slugHeading(heading);
  const h = (info?.headings || []).find((x) => slugHeading(x.text) === want);
  if (!h) return;
  if (mode === 'read') root.querySelector(`.note-read #h-${CSS.escape(want)}`)?.scrollIntoView({ block: 'start' });
  else editor?.gotoLine(h.line);
}

function onReadClick(e) {
  const a = e.target.closest('a');
  const wl = e.target.closest('.wikilink');
  if (wl) { e.preventDefault(); followLink(wl.dataset.note, wl.dataset.heading); return; }
  const et = e.target.closest('.note-embed .embed-title');
  if (et) { const em = et.closest('.note-embed'); followLink(em.dataset.note, em.dataset.heading); return; }
  const cite = e.target.closest('.cite');
  if (cite) {
    e.preventDefault();
    if (!openCitation(cite.dataset.key, cite.dataset.page ? Number(cite.dataset.page) : null)) showCard(cite);
    return;
  }
  const box = e.target.closest('.task-box[data-task-line]');
  if (box) { e.preventDefault(); if (editor?.toggleTask(Number(box.dataset.taskLine))) renderRead(); return; }
  const copy = e.target.closest('.copy-code');
  if (copy) {
    const code = copy.parentElement.querySelector('code')?.textContent || '';
    navigator.clipboard?.writeText(code).then(() => toast('Code copied.', { type: 'success' }), () => {});
    return;
  }
  if (a && a.getAttribute('href') && !a.getAttribute('href').startsWith('#')) { e.preventDefault(); window.open(a.href, '_blank', 'noopener'); }
}

function onReadDblClick(e) {
  if (e.target.closest('a, input, button')) return;
  const block = e.target.closest('[data-line]');
  setMode(editMode);
  if (block) editor?.gotoLine(Number(block.dataset.line) + 1);
}

/** Open a note (saving the current one first). Calls are serialised. */
export function openNote(path) {
  opening = opening.then(() => doOpen(path)).catch(toastError);
  return opening;
}

async function doOpen(path) {
  if (session && session.path === path) { editor?.focus(); return; }
  if (session) {
    await session.save();
    session.dispose();
    session = null;
  }
  const res = await api.getNote(path);
  const ed = await ensureEditor();
  const start = NoteSession.open(path, res, drafts);
  linkData = { notes: new Map(), cites: new Map() };
  linkResp = null;
  ed.load(start.text);
  info = res.info;
  session = new NoteSession({
    path, text: start.text, serverText: res.text, hash: res.hash, drafts,
    delay: state.settings.notes_autosave_ms || 1500,
    api: { save: api.saveNote, get: api.getNote, create: api.recreateNote },
    onStatus: paintStatus,
    onConflict: (c) => conflictDialog(c),
    onRemoteText: (t) => { ed.replace(t); if (mode === 'read') renderRead(); toast('Updated with changes made in another window.'); },
    onSaved: () => afterSave(),
  });
  store('zc.notes.active', path);
  root.classList.add('has-note');
  paintStatus(session.status);
  paintCrumbs();
  paintSide();
  paintTree();
  setMode(mode);
  loadLinks();
  if (start.conflict) await session.startConflict();
  else if (start.restored) toast('Restored changes that had not been saved yet.', { type: 'success' });
}

function paintEmpty() {
  root.classList.remove('has-note');
  root.querySelector('.note-crumbs').innerHTML = '';
  root.querySelector('.note-status').textContent = '';
  root.querySelector('.notes-side').innerHTML = '';
}

async function closeCurrent() {
  if (!session) return;
  session.dispose();
  session = null;
  info = null;
  store('zc.notes.active', null);
  paintEmpty();
  paintTree();
}

let afterSaveTimer = null;
function afterSave() {
  // titles, folders and outline may have changed: refresh lazily
  clearTimeout(afterSaveTimer);
  afterSaveTimer = setTimeout(async () => {
    if (!session) return;
    try {
      const res = await api.getNote(session.path);
      if (session && res.path === session.path) { info = res.info; paintSide(); }
    } catch { /* next save will try again */ }
    loadLinks();
    refreshTree();
  }, 400);
}

/* ================================================================== tree */
export async function refreshTree() {
  try {
    overview = await api.notes();
  } catch (e) { if (notesOpen()) toastError(e); return; }
  emit('notes-count', overview.notes.length);
  if (notesOpen()) paintTree();
}

const stemOf = (p) => p.split('/').pop().replace(/\.md$/i, '');
const parentOf = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');

function buildTree() {
  const folders = new Map([['', { path: '', name: '', folders: [], notes: [] }]]);
  const ensure = (path) => {
    if (folders.has(path)) return folders.get(path);
    const node = { path, name: path.split('/').pop(), folders: [], notes: [] };
    folders.set(path, node);
    ensure(parentOf(path)).folders.push(node);
    return node;
  };
  overview.folders.forEach(ensure);
  for (const n of overview.notes) ensure(parentOf(n.path)).notes.push(n);
  const cmp = (a, b) => a.localeCompare(b, undefined, { sensitivity: 'base', numeric: true });
  for (const f of folders.values()) {
    f.folders.sort((a, b) => cmp(a.name, b.name));
    f.notes.sort((a, b) => cmp(stemOf(a.path), stemOf(b.path)));
  }
  return folders.get('');
}

function paintTree() {
  const el = root.querySelector('.notes-tree');
  const active = session?.path;
  const matches = (n) => !filter || stemOf(n.path).toLowerCase().includes(filter) || (n.title || '').toLowerCase().includes(filter);
  const rows = [];
  const walk = (node, depth) => {
    for (const f of node.folders) {
      const open = filter || !collapsed.has(f.path);
      const before = rows.length;
      rows.push(null); // placeholder for the folder row
      if (open) walk(f, depth + 1);
      const hasHits = rows.length > before + 1;
      if (filter && !hasHits) { rows.length = before; continue; }
      rows[before] = `<div class="nt-row folder" role="treeitem" aria-expanded="${open}" data-folder="${escapeHtml(f.path)}" data-drop-folder="${escapeHtml(f.path)}" draggable="true" style="--d:${depth}">
          <span class="twisty ${open ? 'open' : ''}">${icon('chevron', 'icon-sm')}</span>${icon('folder')}<span class="nm">${escapeHtml(f.name)}</span></div>`;
    }
    for (const n of node.notes) {
      if (!matches(n)) continue;
      const kind = n.type === 'paper' ? 'paper' : '';
      rows.push(`<div class="nt-row note ${n.path === active ? 'active' : ''} ${kind}" role="treeitem" data-path="${escapeHtml(n.path)}" draggable="true"
          title="${escapeHtml(n.title && n.title !== stemOf(n.path) ? `${n.title}\n${n.path}` : n.path)}" style="--d:${depth}">
          <span class="twisty none"></span>${icon(n.type === 'paper' ? 'book' : 'doc')}<span class="nm">${escapeHtml(stemOf(n.path))}</span></div>`);
    }
  };
  walk(buildTree(), 0);
  el.innerHTML = rows.join('') || `<div class="side-empty">${filter ? 'No notes match.' : 'No notes yet. Create one with <b>+</b>.'}</div>`;
}

/* ============================================================== painting */
function paintStatus(status) {
  const el = root.querySelector('.note-status');
  const [label, cls] = STATUS[status] || ['', ''];
  el.className = `note-status ${cls}`;
  el.innerHTML = status === 'conflict'
    ? `${icon('alert', 'icon-sm')} ${label} <button class="btn btn-sm" data-act="show-conflict">Resolve…</button>`
    : `${status === 'saved' ? icon('check', 'icon-sm') : status === 'saving' ? '<span class="spinner"></span>' : ''} ${label}`;
}

function paintCrumbs() {
  const p = session?.path || '';
  const parts = p.split('/');
  root.querySelector('.note-crumbs').innerHTML = parts.map((x, i) => (i === parts.length - 1
    ? `<button class="crumb last" data-act="rename" title="Rename (double-click in the list)">${escapeHtml(x.replace(/\.md$/i, ''))}</button>`
    : `<span class="crumb">${escapeHtml(x)}</span><span class="sep">/</span>`)).join('');
}

function paintSide() {
  const side = root.querySelector('.notes-side');
  if (!info) { side.innerHTML = ''; return; }
  const heads = info.headings || [];
  const minLevel = Math.min(...heads.map((x) => x.level), 6);
  const L = linkResp && linkResp.path_for === session?.path ? linkResp : null;
  const cited = L?.cited || [];
  const back = L?.backlinks || [];
  const unresolved = (L?.unresolved || []).filter((u, i, a) => a.findIndex((x) => x.kind === u.kind && x.target === u.target) === i);
  const citedHtml = cited.length ? `<div class="ns-list">${cited.map((c) => {
    const it = itemInfo(c.key);
    return `<button class="ns-row cite-row${it ? '' : ' missing'}" data-cite-keys="${escapeHtml(c.key)}" data-goto-line="${c.lines[0]}">
      <span class="ns-main">${escapeHtml(it ? [shortAuthors(it.authors), it.year].filter(Boolean).join(' ') || c.key : `@${c.key}`)}${c.count > 1 ? ` <span class="count">×${c.count}</span>` : ''}</span>
      <span class="ns-sub">${escapeHtml(it ? it.title : 'not in your library')}</span></button>`;
  }).join('')}</div>` : '<div class="side-empty">Cite papers with [@key] — type [@ to search your library.</div>';
  const backHtml = back.length ? `<div class="ns-list">${back.map((b) => `
    <button class="ns-row" data-open-path="${escapeHtml(b.path)}" data-open-line="${b.line}">
      <span class="ns-main">${escapeHtml(b.source_title || stemOf(b.path))}</span>
      <span class="ns-sub">${escapeHtml(b.context)}</span></button>`).join('')}</div>` : '<div class="side-empty">No other note links here yet.</div>';
  const unresolvedHtml = unresolved.length ? `<div class="ns-list">${unresolved.map((u) => `
    <button class="ns-row warn" ${u.kind === 'cite' ? `data-cite-keys="${escapeHtml(u.target)}"` : `data-create-note="${escapeHtml(u.target)}"`} data-goto-line="${u.line}">
      <span class="ns-main">${u.kind === 'cite' ? `@${escapeHtml(u.target)}` : `[[${escapeHtml(u.target)}]]`}</span>
      <span class="ns-sub">${u.kind === 'cite' ? 'not in your library' : 'no such note — click to create'}</span></button>`).join('')}</div>` : '';
  side.innerHTML = `
    <div class="ns-section"><div class="ns-title">Cited papers ${cited.length ? `<span class="count">${cited.length}</span>` : ''}</div>${citedHtml}</div>
    <div class="ns-section"><div class="ns-title">Linked from ${back.length ? `<span class="count">${back.length}</span>` : ''}</div>${backHtml}</div>
    ${unresolvedHtml ? `<div class="ns-section"><div class="ns-title">Unresolved <span class="count">${unresolved.length}</span></div>${unresolvedHtml}</div>` : ''}
    <div class="ns-section"><div class="ns-title">Outline</div>
      ${heads.length ? `<div class="outline">${heads.map((x) => `<button class="ol-item" data-outline-line="${x.line}" style="--l:${x.level - minLevel}">${escapeHtml(x.text)}</button>`).join('')}</div>`
        : '<div class="side-empty">Headings (# Title) appear here.</div>'}
    </div>
    <div class="ns-section"><div class="ns-title">Note</div>
      <dl class="ns-meta">
        ${info.type === 'paper' ? `<dt>Paper</dt><dd class="mono">${escapeHtml(info.paper || '')}</dd>` : ''}
        ${(info.tags || []).length ? `<dt>Tags</dt><dd>${info.tags.map((t) => `<span class="tag-pill sm">${escapeHtml(t)}</span>`).join(' ')}</dd>` : ''}
        ${info.created ? `<dt>Created</dt><dd>${escapeHtml(formatDate(info.created, true))}</dd>` : ''}
        ${info.mtime ? `<dt>Modified</dt><dd>${escapeHtml(formatDate(new Date(info.mtime * 1000).toISOString(), true))}</dd>` : ''}
        <dt>File</dt><dd class="mono path">${escapeHtml(info.path)}</dd>
      </dl>
    </div>`;
}

/* ============================================================== actions */
function folderOfSelection() {
  return session ? parentOf(session.path) : '';
}

export async function newNote(folder = folderOfSelection()) {
  const title = await promptDialog('Title', { title: 'New note', placeholder: 'Untitled', confirm: 'Create',
    hint: folder ? `In ${folder}/` : 'At the top level of your notes' });
  if (title === null) return;
  try {
    const res = await api.createNote({ title: title || 'Untitled', folder });
    if (folder) collapsed.delete(folder);
    await refreshTree();
    await openNote(res.path);
  } catch (e) { toastError(e); }
}

async function newFolder(parent = '') {
  const name = await promptDialog('Folder name', { title: 'New folder', confirm: 'Create', hint: parent ? `Inside ${parent}/` : '' });
  if (!name) return;
  try {
    await api.newNoteFolder(parent ? `${parent}/${name}` : name);
    if (parent) collapsed.delete(parent);
    saveCollapsed();
    refreshTree();
  } catch (e) { toastError(e); }
}

async function moveNote(path, to) {
  if (!to || to === path) return;
  try {
    const isOpen = session?.path === path;
    if (isOpen) { await session.save(); if (session.dirty) throw new Error('Save the note before moving it.'); }
    const res = await api.moveNote(path, to, isOpen ? session.saved.hash : undefined);
    if (isOpen) {
      // keep editing the same text under its new name
      const old = session;
      old.dispose();
      session = null;
      await doOpen(res.path);
    }
    await refreshTree();
    if (res.broken_links?.length) {
      const names = [...new Set(res.broken_links.map((b) => stemOf(b.path)))];
      toast(`${plural(res.broken_links.length, 'link')} to the old name in ${names.slice(0, 3).join(', ')}${names.length > 3 ? '…' : ''} no longer resolve${res.broken_links.length === 1 ? 's' : ''}.`,
        { timeout: 9000 });
    }
  } catch (e) { toastError(e); }
}

async function renameNote(path) {
  const stem = stemOf(path);
  const name = await promptDialog('Name', { title: 'Rename note', value: stem, confirm: 'Rename' });
  if (!name || name === stem) return;
  const folder = parentOf(path);
  const clean = name.replace(/\.md$/i, '').replace(/\//g, '-');
  return moveNote(path, `${folder ? `${folder}/` : ''}${clean}.md`);
}

async function trashNote(path) {
  try {
    if (session?.path === path) {
      await session.save();
      await closeCurrent();
    }
    const res = await api.trashNote(path);
    await refreshTree();
    toast(`Moved “${stemOf(path)}” to the notes trash.`, {
      action: { label: 'Undo', run: async () => { try { await api.restoreNote(res.trash_id, res.path); await refreshTree(); openNote(res.path); } catch (e) { toastError(e); } } },
      timeout: 8000,
    });
  } catch (e) { toastError(e); }
}

async function showTrash() {
  let list;
  try { list = (await api.noteTrash()).trash; } catch (e) { toastError(e); return; }
  await openDialog({
    title: 'Deleted notes',
    subtitle: 'Deleted notes are kept in the .trash folder inside your notes folder.',
    wide: true,
    render: (b) => {
      b.innerHTML = list.length ? `<div class="trash-list">${list.map((t, i) => `<div class="trash-row"><span class="mono">${escapeHtml(t.path)}</span>
          <span class="muted">${escapeHtml(t.trash_id.slice(0, 8).replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3'))}</span>
          <button class="btn btn-sm" data-restore="${i}">${icon('restore', 'icon-sm')} Restore</button></div>`).join('')}</div>`
        : '<p class="muted" style="margin:0">The notes trash is empty.</p>';
      b.addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-restore]');
        if (!btn) return;
        const t = list[Number(btn.dataset.restore)];
        try {
          await api.restoreNote(t.trash_id, t.path);
          btn.closest('.trash-row').classList.add('done');
          btn.replaceWith(h('<span class="muted">Restored</span>'));
          refreshTree();
        } catch (err) { toastError(err); }
      });
    },
    buttons: [{ label: 'Close', kind: 'primary' }],
  });
}

async function rescan() {
  try {
    const r = await api.rescanNotes();
    await refreshTree();
    toast(`Notes folder rescanned: ${r.added} added, ${r.changed} changed, ${r.removed} removed.`);
    session?.checkRemote();
  } catch (e) { toastError(e); }
}

/* ============================================================== conflict */
function conflictDialog(c) {
  if (c.deleted) {
    return openDialog({
      title: 'This note was deleted elsewhere',
      subtitle: 'It was moved to the trash or deleted in another window while you were editing it.',
      dismissable: false,
      render: (b) => { b.innerHTML = '<p style="margin:0">Your text is still here. Save it again under the same name, or close it.</p>'; },
      buttons: [{ label: 'Close the note', value: 'close' }, { label: 'Save my text again', kind: 'primary', value: 'mine' }],
    }).then(async (v) => {
      if (v === 'mine') { await session.resolve('mine'); refreshTree(); } else { await closeCurrent(); refreshTree(); }
    });
  }
  const rows = lineDiff(c.theirs, c.mine);
  const diffHtml = rows === null
    ? '<p class="muted">The notes are too long to compare here.</p>'
    : `<div class="diff">${compactDiff(rows).map((r) => (r.op === '…'
      ? `<div class="d-gap">… ${plural(r.count, 'unchanged line')} …</div>`
      : `<div class="d-${r.op === '+' ? 'add' : r.op === '-' ? 'del' : 'same'}"><span class="d-op">${r.op === ' ' ? '' : r.op}</span>${escapeHtml(r.text) || ' '}</div>`)).join('')}</div>`;
  return openDialog({
    title: 'This note was changed elsewhere',
    subtitle: 'Another window (or another app) saved this note while you were editing it. Nothing has been overwritten.',
    wide: true,
    dismissable: false,
    render: (b) => {
      b.innerHTML = `<div class="diff-legend"><span class="d-del">− only in the saved file</span><span class="d-add">+ only in your version</span></div>${diffHtml}`;
    },
    buttons: [{ label: 'Use the saved file', value: 'theirs' }, { label: 'Keep my version', kind: 'primary', value: 'mine' }],
  }).then((v) => session?.resolve(v === 'theirs' ? 'theirs' : 'mine'));
}

/* ================================================================ events */
function saveCollapsed() { store('zc.notes.collapsed', [...collapsed]); }

function onClick(e) {
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act === 'new-note') return newNote();
  if (act === 'new-folder') return newFolder(folderOfSelection());
  if (act === 'rename' && session) return renameNote(session.path);
  if (act === 'show-conflict' && session?.conflict) return conflictDialog(session.conflict);
  if (act === 'tree-menu') {
    return openMenu([
      { label: 'Rescan notes folder', icon: 'refresh', run: rescan },
      { label: 'Deleted notes…', icon: 'trash', run: showTrash },
      'sep',
      { label: 'Collapse all folders', icon: 'minus', run: () => { overview.folders.forEach((f) => collapsed.add(f)); saveCollapsed(); paintTree(); } },
    ], e.target.closest('[data-act]'));
  }
  const mb = e.target.closest('[data-mode]');
  if (mb) return setMode(mb.dataset.mode);
  const op = e.target.closest('[data-open-path]');
  if (op) return openNotes(op.dataset.openPath, Number(op.dataset.openLine) || null);
  const cn = e.target.closest('[data-create-note]');
  if (cn) return followLink(cn.dataset.createNote, '');
  const cr = e.target.closest('.ns-row[data-cite-keys]');
  if (cr) { gotoLine(Number(cr.dataset.gotoLine)); showCard(cr); return; }
  const ol = e.target.closest('[data-outline-line]');
  if (ol) {
    const line = Number(ol.dataset.outlineLine);
    if (mode === 'read') {
      const els = [...root.querySelectorAll('.note-read [data-line]')];
      const el = els.find((x) => Number(x.dataset.line) + 1 >= line);
      el?.scrollIntoView({ block: 'start' });
      return;
    }
    return editor?.gotoLine(line);
  }
  const folder = e.target.closest('.nt-row.folder');
  if (folder) {
    const p = folder.dataset.folder;
    collapsed.has(p) ? collapsed.delete(p) : collapsed.add(p);
    saveCollapsed();
    return paintTree();
  }
  const note = e.target.closest('.nt-row.note');
  if (note) openNote(note.dataset.path);
}

function onDblClick(e) {
  const note = e.target.closest('.nt-row.note');
  if (note) renameNote(note.dataset.path);
}

function onContextMenu(e) {
  const note = e.target.closest('.nt-row.note');
  const folder = e.target.closest('.nt-row.folder');
  const blank = !note && !folder && e.target.closest('.notes-tree');
  if (!note && !folder && !blank) return;
  e.preventDefault();
  const at = { x: e.clientX, y: e.clientY };
  if (note) {
    const p = note.dataset.path;
    return openMenu([
      { label: 'Open', icon: 'open', run: () => openNote(p) },
      { label: 'Rename…', icon: 'edit', run: () => renameNote(p) },
      { label: 'Move to folder…', icon: 'folder', run: async () => {
        const to = await promptDialog('Folder (empty = top level)', { title: 'Move note', value: parentOf(p), confirm: 'Move' });
        if (to !== null) moveNote(p, `${to.replace(/^\/+|\/+$/g, '')}${to.trim() ? '/' : ''}${p.split('/').pop()}`);
      } },
      { label: 'Copy link', icon: 'link', run: () => { navigator.clipboard?.writeText(`[[${stemOf(p)}]]`); toast(`Copied [[${stemOf(p)}]]`); } },
      'sep',
      { label: 'Move to Trash', icon: 'trash', danger: true, run: async () => {
        if (state.settings.confirm_trash && !(await confirmDialog(`Move “${stemOf(p)}” to the notes trash?`, { confirm: 'Move to Trash', danger: true }))) return;
        trashNote(p);
      } },
    ], at);
  }
  const fp = folder ? folder.dataset.folder : '';
  openMenu([
    { label: 'New note here', icon: 'plus', run: () => newNote(fp) },
    { label: 'New folder here', icon: 'folderPlus', run: () => newFolder(fp) },
  ], at);
}

function bindDnD() {
  const tree = root.querySelector('.notes-tree');
  tree.addEventListener('dragstart', (e) => {
    const row = e.target.closest('.nt-row.note');
    if (!row) { e.preventDefault(); return; }
    e.dataTransfer.setData(NOTE_MIME, row.dataset.path);
    e.dataTransfer.setData('text/plain', `[[${stemOf(row.dataset.path)}]]`);  // dropping into the editor links it
    e.dataTransfer.effectAllowed = 'copyMove';
  });
  const target = (e) => e.target.closest('[data-drop-folder]');
  tree.addEventListener('dragover', (e) => {
    if (![...e.dataTransfer.types].includes(NOTE_MIME)) return;
    const t = target(e);
    if (!t) return;
    e.preventDefault();
    tree.querySelectorAll('.drop-target').forEach((x) => x.classList.remove('drop-target'));
    if (t !== tree) t.classList.add('drop-target');
  });
  tree.addEventListener('dragleave', (e) => { if (e.target.classList?.contains('drop-target')) e.target.classList.remove('drop-target'); });
  tree.addEventListener('drop', (e) => {
    const path = e.dataTransfer.getData(NOTE_MIME);
    tree.querySelectorAll('.drop-target').forEach((x) => x.classList.remove('drop-target'));
    const t = target(e);
    if (!path || !t) return;
    e.preventDefault();
    const folder = t.dataset.dropFolder;
    if (folder === parentOf(path)) return;
    moveNote(path, `${folder ? `${folder}/` : ''}${path.split('/').pop()}`);
  });
}

/** Keyboard shortcuts while the notes workspace is open (called from main.js). */
export function notesShortcut(e, k, mod) {
  if (mod && k === 'e' && session && !e.target.closest?.('.cm-editor')) { e.preventDefault(); setMode(mode === 'read' ? editMode : 'read'); return true; }
  if (mod && k === 'n' && !e.shiftKey) { e.preventDefault(); newNote(); return true; }
  if (mod && k === 'f' && !e.target.closest?.('.cm-editor')) { e.preventDefault(); root.querySelector('.notes-filter input').focus(); return true; }
  return false;
}
