// Notes workspace: file tree · editor · outline. Opened from "Notes" in the sidebar; it
// covers the item list and item pane (which keep their state underneath).
//
// Saving is handled by NoteSession (noteSession.js): autosave, conflict detection,
// offline retries and local drafts. This module only wires it to the UI.
import { api, openAttachment } from '../../api.js';
import { icon } from '../../icons.js';
import { emit, on, state } from '../../store.js';
import { confirmDialog, openDialog, promptDialog } from '../../ui/dialog.js';
import { openMenu } from '../../ui/menu.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml, formatDate, h, modKey, modLabel, plural, storage, store } from '../../util.js';
import { navPush } from '../../nav.js';
import { closeGlobal, globalOpen, graphChanged, initNotesGraph, localSection, openGlobal, showLocal } from '../graph/notesGraph.js';
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
let hitTimer = null;
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
        <button class="icon-btn" data-act="graph" title="Graph of notes and papers (${modLabel}⇧G)">${icon('link')}</button>
        <button class="icon-btn" data-act="tree-menu" title="More">${icon('drag', 'icon-sm')}</button>
      </div>
      <div class="notes-filter"><input class="input" placeholder="Filter notes" spellcheck="false" aria-label="Filter notes"></div>
      <div class="notes-tree" role="tree" aria-multiselectable="true" tabindex="0" data-drop-folder=""></div>
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
    <aside class="notes-side" aria-label="Note details"><div class="ns-details"></div></aside>
  </div>`);
  document.getElementById('app').appendChild(root);
  root.querySelector('.notes-side').prepend(localSection());
  initNotesGraph({
    root,
    openNote: (p) => openNote(p),
    currentPath: () => session?.path || null,
    openGraph: (focus) => openNotesGraph(focus),
    onGlobalClosed: () => navPush({ ws: 'notes', path: session?.path || null }),
  });

  root.addEventListener('click', onClick);
  root.addEventListener('dblclick', onDblClick);
  window.zcNotes = { text: () => session?.text ?? null, path: () => session?.path ?? null };   // read-only, for tests
  listenForQuotes();
  root.querySelector('.note-read').addEventListener('click', onReadClick);
  attachCiteCards(root, { itemInfo, openNote: (p) => openNote(p), refreshLibrary: async () => { await loadLibrary(); await refreshTree(); } });
  let libTimer = null;
  on('items', () => { if (notesOpen()) { clearTimeout(libTimer); libTimer = setTimeout(loadLibrary, 500); } });
  root.querySelector('.note-read').addEventListener('dblclick', onReadDblClick);
  root.addEventListener('contextmenu', onContextMenu);
  root.querySelector('.notes-filter input').addEventListener('input', (e) => {
    filter = e.target.value.trim().toLowerCase();
    paintTree();
    clearTimeout(hitTimer);
    hitTimer = setTimeout(paintHits, 250);
  });
  bindDnD();
  root.querySelector('.notes-tree').addEventListener('keydown', onTreeKey);

  window.addEventListener('focus', () => { if (notesOpen()) { session?.checkRemote(); refreshTree(); } });
  document.addEventListener('visibilitychange', () => { if (document.hidden) session?.save(); });
  window.addEventListener('pagehide', flushOnUnload);
  // Diagrams are drawn for one theme: redraw them when light/dark changes.
  new MutationObserver(() => { if (!notesOpen() || !session) return; editor?.refresh(); if (mode === 'read') renderRead(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  on('search', () => { if (notesOpen() && state.query) closeNotes(); });   // a library search shows its results
  on('settings', () => { if (!notesEnabled() && notesOpen()) closeNotes(); emit('notes-count', notesEnabled() ? overview.notes.length : null); });

  if (notesEnabled()) refreshTree().then(() => { if (storage('zc.notes.open', false)) openNotes(); });
}

/**
 * The PDF viewer (another tab) can send quotes and figures into the note open here.
 * Messages on BroadcastChannel 'zc-notes':
 *   viewer → {type:'who', req}                       every Notes tab with an open note answers
 *   notes  → {type:'here', req, tab, path, title}
 *   viewer → {type:'insert', req, tab, markdown}     only the addressed tab inserts
 *   notes  → {type:'inserted', req, tab, path}
 */
const TAB_ID = Math.random().toString(36).slice(2);
function listenForQuotes() {
  if (!('BroadcastChannel' in window)) return;
  const ch = new BroadcastChannel('zc-notes');
  ch.onmessage = (e) => {
    const m = e.data || {};
    if (m.type === 'who' && notesOpen() && session && editor && session.status !== 'conflict') {
      ch.postMessage({ type: 'here', req: m.req, tab: TAB_ID, path: session.path, title: info?.title || stemOf(session.path) });
    }
    if (m.type === 'insert' && m.tab === TAB_ID && session && editor) {
      editor.insertBlock(String(m.markdown || ''));
      if (mode === 'read') renderRead();
      toast('Added from the PDF.', { type: 'success' });
      ch.postMessage({ type: 'inserted', req: m.req, tab: TAB_ID, path: session.path });
    }
  };
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
export async function openNotes(path = null, line = null, { graph = false, focus = null } = {}) {
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
  } else {
    if (path) toast(`“${stemOf(path)}” no longer exists (moved or deleted).`);
    if (!session) paintEmpty();
  }
  if (graph) await openGlobal(focus); else closeGlobal();
  navPush(graph ? { ws: 'notes', path: session?.path || null, graph: true, focus } : { ws: 'notes', path: session?.path || null });
}

/** The full graph in Notes, optionally centred on a node ("note:<path>", "item:<id>"). */
export async function openNotesGraph(focus = null) {
  if (!notesEnabled()) return;
  if (!notesOpen()) await openNotes();
  await openGlobal(focus);
  navPush({ ws: 'notes', path: session?.path || null, graph: true, focus });
}

/** ⌘⇧G: the graph, or back to the note. */
export async function toggleNotesGraph() {
  if (notesOpen() && globalOpen()) { closeGlobal({ push: true }); editor?.focus(); } else await openNotesGraph(session ? `note:${session.path}` : null);
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
  navPush({ ws: 'library', view: state.view, sel: [...state.selection] });
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

function setMode(m, { focus = true } = {}) {
  if (!['live', 'source', 'read'].includes(m)) m = 'live';
  mode = m;
  if (m !== 'read') editMode = m;
  store('zc.notes.mode', m);
  root.querySelectorAll('[data-mode]').forEach((b) => b.classList.toggle('active', b.dataset.mode === m));
  const read = root.querySelector('.note-read');
  root.classList.toggle('reading', m === 'read');
  read.hidden = m !== 'read';
  if (m === 'read') { renderRead(); if (focus) read.focus(); return; }
  editor?.setMode(m);
  if (focus) editor?.focus();
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
  closeGlobal();
  opening = opening.then(() => doOpen(path)).catch(toastError);
  return opening;
}

async function doOpen(path) {
  if (session && session.path === path) { if (!treeEl().contains(document.activeElement)) editor?.focus(); return; }
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
  if (notesOpen()) navPush({ ws: 'notes', path });
  if (!sel.has(nKey(path))) selectOnly(nKey(path));   // opened from elsewhere: the list follows
  root.classList.add('has-note');
  showLocal(path);
  paintStatus(session.status);
  paintCrumbs();
  paintSide();
  paintTree();
  // opened from the file list: the keyboard stays there (arrows, Delete) until you click the note
  setMode(mode, { focus: !treeEl().contains(document.activeElement) });
  loadLinks();
  if (start.conflict) await session.startConflict();
  else if (start.restored) toast('Restored changes that had not been saved yet.', { type: 'success' });
}

function paintEmpty() {
  root.classList.remove('has-note');
  showLocal(null);
  root.querySelector('.note-crumbs').innerHTML = '';
  root.querySelector('.note-status').textContent = '';
  root.querySelector('.notes-side .ns-details').innerHTML = '';
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
    graphChanged();
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
const nameOf = (p) => p.split('/').pop();
const within = (p, folder) => p.startsWith(`${folder}/`);

// Selection in the tree, as row keys: 'n:<path>' for notes, 'f:<path>' for folders.
let sel = new Set();
let anchor = null;      // where a shift-click / shift-arrow range starts
let cursor = null;      // the row the keyboard is on
const nKey = (p) => `n:${p}`;
const fKey = (p) => `f:${p}`;
const keyOf = (row) => (row.classList.contains('folder') ? fKey(row.dataset.folder) : nKey(row.dataset.path));
const treeEl = () => root.querySelector('.notes-tree');
const visibleKeys = () => [...treeEl().querySelectorAll('.nt-row')].map(keyOf);

function selectOnly(key) { sel = new Set(key ? [key] : []); anchor = cursor = key; }
function toggleSelected(key) { sel.has(key) ? sel.delete(key) : sel.add(key); anchor = cursor = key; }
function selectRange(key) {
  const keys = visibleKeys();
  const b = keys.indexOf(key);
  const a = keys.includes(anchor) ? keys.indexOf(anchor) : b;
  sel = new Set(keys.slice(Math.min(a, b), Math.max(a, b) + 1));
  cursor = key;
}
/** The selected notes and folders (a note inside a selected folder goes with the folder). */
function selected() {
  const folders = [...sel].filter((k) => k[0] === 'f').map((k) => k.slice(2));
  const notes = [...sel].filter((k) => k[0] === 'n').map((k) => k.slice(2)).filter((p) => !folders.some((f) => within(p, f)));
  return { notes, folders: folders.filter((f) => !folders.some((o) => within(f, o))) };
}

function buildTree() {
  const folders = new Map([['', { path: '', name: '', folders: [], notes: [] }]]);
  const ensure = (path) => {
    if (folders.has(path)) return folders.get(path);
    const node = { path, name: nameOf(path), folders: [], notes: [] };
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
  const el = treeEl();
  const active = session?.path;
  const matches = (n) => !filter || stemOf(n.path).toLowerCase().includes(filter) || (n.title || '').toLowerCase().includes(filter);
  const cls = (key) => `${sel.has(key) ? ' selected' : ''}${cursor === key ? ' cursor' : ''}`;
  const rows = [];
  const walk = (node, depth) => {
    for (const f of node.folders) {
      const open = filter || !collapsed.has(f.path);
      const before = rows.length;
      rows.push(null); // placeholder for the folder row
      if (open) walk(f, depth + 1);
      const hasHits = rows.length > before + 1;
      if (filter && !hasHits) { rows.length = before; continue; }
      const k = fKey(f.path);
      rows[before] = `<div class="nt-row folder${cls(k)}" role="treeitem" aria-expanded="${!!open}" aria-selected="${sel.has(k)}" data-folder="${escapeHtml(f.path)}" data-drop-folder="${escapeHtml(f.path)}" draggable="true" style="--d:${depth}">
          <span class="twisty ${open ? 'open' : ''}">${icon('chevron', 'icon-sm')}</span>${icon('folder')}<span class="nm">${escapeHtml(f.name)}</span></div>`;
    }
    for (const n of node.notes) {
      if (!matches(n)) continue;
      const k = nKey(n.path);
      rows.push(`<div class="nt-row note${n.path === active ? ' active' : ''}${n.type === 'paper' ? ' paper' : ''}${cls(k)}" role="treeitem" aria-selected="${sel.has(k)}" data-path="${escapeHtml(n.path)}" draggable="true"
          title="${escapeHtml(n.title && n.title !== stemOf(n.path) ? `${n.title}\n${n.path}` : n.path)}" style="--d:${depth}">
          <span class="twisty none"></span>${icon(n.type === 'paper' ? 'book' : 'doc')}<span class="nm">${escapeHtml(stemOf(n.path))}</span></div>`);
    }
  };
  walk(buildTree(), 0);
  el.innerHTML = rows.join('') || (filter ? '' : '<div class="side-empty">No notes yet. Create one with <b>+</b>.</div>');
  // Only what can be seen stays selected, so Delete never takes hidden notes with it.
  const shown = new Set(visibleKeys());
  sel = new Set([...sel].filter((k) => shown.has(k)));
  if (cursor && !shown.has(cursor)) cursor = null;
  if (filter.length >= 2) { clearTimeout(hitTimer); hitTimer = setTimeout(paintHits, 150); }
}

/** Show a selection change on the rows already drawn (no redraw, so double-clicks still land). */
function paintSelection() {
  for (const row of treeEl().querySelectorAll('.nt-row')) {
    const k = keyOf(row);
    row.classList.toggle('selected', sel.has(k));
    row.classList.toggle('cursor', cursor === k);
    row.setAttribute('aria-selected', String(sel.has(k)));
  }
}

/** Notes whose text contains the filter words (below the name matches in the tree). */
async function paintHits() {
  const el = treeEl();
  el.querySelector('.notes-hits')?.remove();
  const q = filter;
  if (q.length < 2) return;
  let res;
  try { res = (await api.searchNotes(q)).results; } catch { return; }
  if (q !== filter) return;
  const nameHits = new Set([...el.querySelectorAll('.nt-row.note')].map((r) => r.dataset.path));
  const rows = res.filter((r) => !nameHits.has(r.path));
  if (!rows.length) return;
  el.querySelector('.notes-hits')?.remove();
  el.insertAdjacentHTML('beforeend', `<div class="notes-hits"><div class="ns-title">Text matches</div>${rows.map((r) => `
    <button class="nt-hit" data-path="${escapeHtml(r.path)}"><div class="nm">${escapeHtml(r.title || stemOf(r.path))}</div>
      <div class="snip">${r.snippet}</div></button>`).join('')}</div>`);
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
  // A paper note links straight back to its item and PDF.
  const it = info?.type === 'paper' && info.paper ? itemInfo(info.paper) : null;
  const paper = it ? `<span class="note-paper">
      <button class="btn btn-sm btn-ghost" data-act="paper-item" title="Show “${escapeHtml(it.title)}” in the library">${icon('library', 'icon-sm')} Show in library</button>
      ${it.pdf ? `<button class="btn btn-sm btn-ghost" data-act="paper-pdf" title="Open the PDF">${icon('pdf', 'icon-sm')} PDF</button>` : ''}</span>` : '';
  root.querySelector('.note-crumbs').innerHTML = parts.map((x, i) => (i === parts.length - 1
    ? `<button class="crumb last" data-act="rename" title="Rename (F2 in the list)">${escapeHtml(x.replace(/\.md$/i, ''))}</button>`
    : `<span class="crumb">${escapeHtml(x)}</span><span class="sep">/</span>`)).join('') + paper;
}

function paintSide() {
  const side = root.querySelector('.notes-side .ns-details');   // the local graph above it stays
  if (!info) { side.innerHTML = ''; return; }
  const heads = info.headings || [];
  const minLevel = Math.min(...heads.map((x) => x.level), 6);
  const L = linkResp && linkResp.path_for === session?.path ? linkResp : null;
  const cited = L?.cited || [];
  const back = L?.backlinks || [];
  const unresolved = (L?.unresolved || []).filter((u, i, a) => a.findIndex((x) => x.kind === u.kind && x.target === u.target) === i);
  const citedHtml = cited.length ? `<div class="ns-list">${cited.map((c) => {
    const it = itemInfo(c.key);
    return `<button class="ns-row cite-row${it ? '' : ' missing'}${it?.trashed ? ' trashed' : ''}" data-cite-keys="${escapeHtml(c.key)}" data-goto-line="${c.lines[0]}" title="${it?.trashed ? 'This item is in the Trash' : ''}">
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
  const f = cursor?.startsWith('f:') ? cursor.slice(2) : null;
  return f ?? (session ? parentOf(session.path) : '');
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
    const res = await api.newNoteFolder(parent ? `${parent}/${name}` : name);
    if (parent) collapsed.delete(parent);
    saveCollapsed();
    selectOnly(fKey(res.path));
    refreshTree();
  } catch (e) { toastError(e); }
}

function reportBroken(broken) {
  if (!broken?.length) return;
  const names = [...new Set(broken.map((b) => stemOf(b.path)))];
  toast(`${plural(broken.length, 'link')} to the old name in ${names.slice(0, 3).join(', ')}${names.length > 3 ? '…' : ''} no longer resolve${broken.length === 1 ? 's' : ''}.`,
    { timeout: 9000 });
}

async function moveNote(path, to, { quiet = false } = {}) {
  if (!to || to === path) return;
  try {
    const isOpen = session?.path === path;
    if (isOpen) { await session.save(); if (session.dirty) throw new Error('Save the note before moving it.'); }
    const res = await api.moveNote(path, to, isOpen ? session.saved.hash : undefined);
    if (isOpen) await reopenAs(res.path);
    if (sel.delete(nKey(path))) sel.add(nKey(res.path));
    if (cursor === nKey(path)) anchor = cursor = nKey(res.path);
    if (!quiet) await refreshTree();
    reportBroken(res.broken_links);
  } catch (e) { toastError(e); }
}

/** After a dialog, give the keyboard back to the file list if that is where it was. */
function keepTreeFocus() {
  const was = treeEl().contains(document.activeElement);
  return () => { if (was) treeEl().focus({ preventScroll: true }); };
}

/** The open note was moved: keep editing it under its new path, with anything typed meanwhile. */
async function reopenAs(path) {
  const typed = session.text;
  session.dispose();
  session = null;
  await doOpen(path);
  if (session && typed !== session.text) editor.replace(typed);   // saved by the new session
}

async function renameNote(path) {
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
async function moveFolder(path, to, { quiet = false } = {}) {
  if (!to || to === path) return;
  try {
    const open = session?.path;
    const inside = open && within(open, path);
    if (inside) { await session.save(); if (session.dirty) throw new Error('Save the open note before moving its folder.'); }
    const res = await api.moveNoteFolder(path, to);
    const moved = (p) => (p === path || within(p, path) ? res.path + p.slice(path.length) : p);
    collapsed = new Set([...collapsed].map(moved));
    saveCollapsed();
    const keep = { sel: [...sel], cursor };   // reopening the note would move the selection to it
    if (inside) await reopenAs(moved(open));
    sel = new Set(keep.sel.map((k) => k[0] + ':' + moved(k.slice(2))));
    anchor = cursor = keep.cursor ? keep.cursor[0] + ':' + moved(keep.cursor.slice(2)) : null;
    if (!quiet) await refreshTree();
    reportBroken(res.broken_links);
  } catch (e) { toastError(e); }
}

async function renameFolder(path) {
  const name = nameOf(path);
  const back = keepTreeFocus();
  const nu = await promptDialog('Name', { title: 'Rename folder', value: name, confirm: 'Rename' });
  back();
  if (!nu || nu.trim() === name) return;
  const parent = parentOf(path);
  return moveFolder(path, `${parent ? `${parent}/` : ''}${nu.trim().replace(/\//g, '-')}`);
}

/** Move notes and folders into ``dest`` ('' = top level); things already there are skipped. */
async function moveItems({ notes, folders }, dest) {
  const into = (p) => `${dest ? `${dest}/` : ''}${nameOf(p)}`;
  for (const f of folders) {
    if (f === dest || within(dest, f) || parentOf(f) === dest) continue;
    await moveFolder(f, into(f), { quiet: true });
  }
  for (const n of notes) {
    if (parentOf(n) !== dest) await moveNote(n, into(n), { quiet: true });
  }
  if (dest) { collapsed.delete(dest); saveCollapsed(); }
  await refreshTree();
}

/** A menu of folders to move things into. */
function pickFolder(at, items) {
  const bad = (f) => items.folders.some((x) => f === x || within(f, x));
  const m = openMenu([
    { section: 'Move to' },
    { label: 'Top level', icon: 'folder', run: () => moveItems(items, '') },
    ...overview.folders.filter((f) => !bad(f)).map((f) => ({ label: f, icon: 'folder', run: () => moveItems(items, f) })),
  ], at);
  m.style.maxHeight = '60vh';
  m.style.overflow = 'auto';
}

function describe({ notes, folders }) {
  const parts = [];
  if (folders.length === 1) parts.push(`folder “${nameOf(folders[0])}”`);
  else if (folders.length) parts.push(`${folders.length} folders`);
  if (notes.length === 1) parts.push(`“${stemOf(notes[0])}”`);
  else if (notes.length) parts.push(plural(notes.length, 'note'));
  return parts.join(' and ');
}

function copyLinks(notes) {
  const text = notes.map((p) => `[[${stemOf(p)}]]`).join('\n');
  navigator.clipboard?.writeText(text);
  toast(notes.length === 1 ? `Copied ${text}` : `Copied ${plural(notes.length, 'link')}`);
}

/** Move the selected notes and folders to the notes trash (one Undo brings them all back). */
async function deleteSelection() {
  const items = selected();
  const { notes, folders } = items;
  if (!notes.length && !folders.length) return;
  const inFolders = overview.notes.filter((n) => folders.some((f) => within(n.path, f))).length;
  const what = describe(items);
  if ((state.settings.confirm_trash || inFolders) && !(await confirmDialog(
    `Move ${what}${inFolders ? ` (with ${plural(inFolders, 'note')} inside)` : ''} to the notes trash?`,
    { title: 'Delete', confirm: 'Move to Trash', danger: true, detail: 'You can undo this, or restore them later from “Deleted notes…”.' }))) return;
  // the row to land on afterwards: the next one that stays
  const gone = (k) => (k[0] === 'n' && notes.includes(k.slice(2))) || folders.some((f) => k.slice(2) === f || within(k.slice(2), f));
  const keys = visibleKeys();
  const last = Math.max(...keys.map((k, i) => (gone(k) ? i : -1)));
  const next = keys.slice(last + 1).find((k) => !gone(k)) || keys.slice(0, last).reverse().find((k) => !gone(k)) || null;
  try {
    const open = session?.path;
    const closing = open && (notes.includes(open) || folders.some((f) => within(open, f)));
    if (closing) { await session.save(); await closeCurrent(); }
    const res = await api.trashNotes(notes, folders);
    selectOnly(next);
    await refreshTree();
    treeEl().focus({ preventScroll: true });
    toast(`Moved ${what} to the notes trash.`, {
      timeout: 9000,
      action: { label: 'Undo', run: async () => {
        try {
          const r = await api.restoreNoteBatch(res.trash_id);
          sel = new Set([...r.paths.filter((x) => !folders.some((f) => within(x, f))).map(nKey), ...folders.map(fKey)]);
          anchor = cursor = [...sel][0] || null;
          await refreshTree();
          if (closing && r.paths.includes(open)) openNote(open);
          if (r.skipped.length) toast(`${plural(r.skipped.length, 'file')} stayed in the trash: something new has the same name.`, { timeout: 9000 });
        } catch (e) { toastError(e); }
      } },
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

function toggleFolder(path, open = collapsed.has(path)) {
  open ? collapsed.delete(path) : collapsed.add(path);
  saveCollapsed();
  paintTree();
}

function onClick(e) {
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act === 'new-note') return newNote();
  if (act === 'new-folder') return newFolder(folderOfSelection());
  if (act === 'graph') return openNotesGraph(session ? `note:${session.path}` : null);
  if (act === 'rename' && session) return renameNote(session.path);
  if (act === 'show-conflict' && session?.conflict) return conflictDialog(session.conflict);
  if (act === 'paper-item' && info?.paper) { const it = itemInfo(info.paper); if (it) emit('show-item', it.id); return; }
  if (act === 'paper-pdf' && info?.paper) { const it = itemInfo(info.paper); if (it?.pdf) openAttachment({ id: it.id, title: it.title }, it.pdf); return; }
  if (act === 'tree-menu') {
    return openMenu([
      { label: 'Rescan notes folder', icon: 'refresh', run: rescan },
      { label: 'Deleted notes…', icon: 'trash', run: showTrash },
      'sep',
      { label: 'Collapse all folders', icon: 'minus', run: () => { overview.folders.forEach((f) => collapsed.add(f)); saveCollapsed(); paintTree(); } },
      { label: 'Expand all folders', icon: 'plus', run: () => { collapsed.clear(); saveCollapsed(); paintTree(); } },
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
  const hit = e.target.closest('.nt-hit');
  if (hit) return openNote(hit.dataset.path);
  const row = e.target.closest('.nt-row');
  if (!row) return;
  // Clicking in the tree keeps the keyboard there (arrows, Delete, F2) until you click the note.
  treeEl().focus({ preventScroll: true });
  const key = keyOf(row);
  if (e.shiftKey) { selectRange(key); return paintSelection(); }
  if (modKey(e)) { toggleSelected(key); return paintSelection(); }
  selectOnly(key);
  if (row.classList.contains('folder')) return toggleFolder(row.dataset.folder);
  paintSelection();
  openNote(row.dataset.path);
}

function onDblClick(e) {
  if (e.shiftKey || modKey(e)) return;
  const note = e.target.closest('.nt-row.note');
  if (note) renameNote(note.dataset.path);
}

/** Keys in the file tree: arrows move (Shift extends), Enter opens, F2 renames, Delete trashes. */
function onTreeKey(e) {
  if (e.target.closest('.nt-hit')) return;   // a "Text matches" button handles its own keys
  const keys = visibleKeys();
  if (!keys.length || e.altKey) return;
  const mod = modKey(e);
  const i = keys.indexOf(cursor);
  const isFolder = cursor?.startsWith('f:');
  const path = cursor?.slice(2);
  const go = (j) => {
    const k = keys[Math.max(0, Math.min(keys.length - 1, j))];
    if (e.shiftKey) selectRange(k); else selectOnly(k);
    paintSelection();
    treeEl().querySelector('.nt-row.cursor')?.scrollIntoView({ block: 'nearest' });
  };
  let handled = true;
  switch (e.key) {
    case 'ArrowDown': go(i < 0 ? 0 : i + 1); break;
    case 'ArrowUp': go(i < 0 ? keys.length - 1 : i - 1); break;
    case 'Home': go(0); break;
    case 'End': go(keys.length - 1); break;
    case 'ArrowRight':
      if (isFolder && collapsed.has(path) && !filter) toggleFolder(path, true);
      else if (isFolder) go(i + 1);
      break;
    case 'ArrowLeft': {
      if (isFolder && !collapsed.has(path) && !filter) { toggleFolder(path, false); break; }
      const up = path ? keys.indexOf(fKey(parentOf(path))) : -1;
      if (up >= 0) go(up);
      break;
    }
    case 'Enter':
      if (!cursor || mod) { handled = false; break; }
      if (isFolder) toggleFolder(path);
      else openNote(path).then(() => (mode === 'read' ? root.querySelector('.note-read').focus() : editor?.focus()));
      break;
    case 'F2':
      if (cursor) (isFolder ? renameFolder : renameNote)(path);
      break;
    case 'Delete': case 'Backspace':
      deleteSelection();
      break;
    case 'Escape':
      if (sel.size > 1 || !session) selectOnly(session ? nKey(session.path) : null); else handled = false;
      paintSelection();
      break;
    default:
      if (mod && e.key.toLowerCase() === 'a') { sel = new Set(keys); paintSelection(); } else handled = false;
  }
  if (handled) { e.preventDefault(); e.stopPropagation(); }
}

function onContextMenu(e) {
  const row = e.target.closest('.nt-row');
  const blank = !row && e.target.closest('.notes-tree');
  if (!row && !blank) return;
  e.preventDefault();
  const at = { x: e.clientX, y: e.clientY };
  if (row && !sel.has(keyOf(row))) { selectOnly(keyOf(row)); paintSelection(); }
  const items = row ? selected() : { notes: [], folders: [] };
  const { notes, folders } = items;
  const trash = { label: 'Move to Trash', icon: 'trash', danger: true, shortcut: '⌫', run: deleteSelection };
  if (row && notes.length + folders.length > 1) {
    return openMenu([
      { section: describe(items) },
      { label: 'Move to folder…', icon: 'folder', run: () => pickFolder(at, items) },
      notes.length && { label: notes.length > 1 ? 'Copy links' : 'Copy link', icon: 'link', run: () => copyLinks(notes) },
      'sep',
      trash,
    ], at);
  }
  if (row && notes.length === 1) {
    const p = notes[0];
    return openMenu([
      { label: 'Open', icon: 'open', run: () => openNote(p), shortcut: '↵' },
      { label: 'Rename…', icon: 'edit', run: () => renameNote(p), shortcut: 'F2' },
      { label: 'Move to folder…', icon: 'folder', run: () => pickFolder(at, items) },
      { label: 'Copy link', icon: 'link', run: () => copyLinks([p]) },
      { label: 'Show in Graph', icon: 'link', run: () => openNotesGraph(`note:${p}`) },
      'sep',
      trash,
    ], at);
  }
  const fp = folders[0] || '';
  openMenu([
    { label: 'New note here', icon: 'plus', run: () => newNote(fp) },
    { label: 'New folder here', icon: 'folderPlus', run: () => newFolder(fp) },
    fp && 'sep',
    fp && { label: 'Rename…', icon: 'edit', run: () => renameFolder(fp), shortcut: 'F2' },
    fp && { label: 'Move to folder…', icon: 'folder', run: () => pickFolder(at, items) },
    fp && 'sep',
    fp && trash,
  ], at);
}

function bindDnD() {
  const tree = treeEl();
  tree.addEventListener('dragstart', (e) => {
    const row = e.target.closest('.nt-row');
    if (!row) { e.preventDefault(); return; }
    const key = keyOf(row);
    // drag the whole selection when the row is part of it, otherwise just this row
    const items = sel.has(key) ? selected()
      : (key[0] === 'f' ? { notes: [], folders: [key.slice(2)] } : { notes: [key.slice(2)], folders: [] });
    e.dataTransfer.setData(NOTE_MIME, JSON.stringify(items));
    if (items.notes.length) e.dataTransfer.setData('text/plain', items.notes.map((p) => `[[${stemOf(p)}]]`).join('\n'));  // dropping into the editor links them
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
    tree.querySelectorAll('.drop-target').forEach((x) => x.classList.remove('drop-target'));
    let items;
    try { items = JSON.parse(e.dataTransfer.getData(NOTE_MIME) || 'null'); } catch { items = null; }
    const t = target(e);
    if (!items || !t) return;
    e.preventDefault();
    moveItems({ notes: items.notes || [], folders: items.folders || [] }, t.dataset.dropFolder);
  });
}

/** Keyboard shortcuts while the notes workspace is open (called from main.js). */
export function notesShortcut(e, k, mod) {
  if (mod && k === 'e' && session && !e.target.closest?.('.cm-editor')) { e.preventDefault(); setMode(mode === 'read' ? editMode : 'read'); return true; }
  if (mod && k === 'n' && !e.shiftKey) { e.preventDefault(); newNote(); return true; }
  if (mod && k === 'f' && !e.target.closest?.('.cm-editor')) { e.preventDefault(); root.querySelector('.notes-filter input').focus(); return true; }
  return false;
}
