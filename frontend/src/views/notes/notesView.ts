// Notes workspace: file tree · editor · outline. Opened from "Notes" in the sidebar; it
// covers the item list and item pane (which keep their state underneath).
//
// Saving is handled by NoteSession (noteSession.ts): autosave, conflict detection, offline
// retries and local drafts. This module sets the workspace up, opens and closes notes and wires
// the session to the UI. The other parts: workspace.ts (shared state), tree.ts and treeInput.ts
// (the notes list), fileOps.ts (new, rename, move, trash), readView.ts (read mode and links),
// sidePanel.ts (status, breadcrumbs, details), aiBar.ts (Claude's edits) and conflict.ts.
import { api, openAttachment } from '../../api.js';
import { icon } from '../../icons.js';
import { navPush } from '../../nav.js';
import { emit, on, state } from '../../store.js';
import { openMenu } from '../../ui/menu.js';
import { toast, toastError } from '../../ui/toast.js';
import { h, modLabel, storage, store } from '../../util.js';
import { closeGlobal, globalOpen, graphChanged, initNotesGraph, localSection, openGlobal, showLocal } from '../graph/notesGraph.js';
import { loadAiEdits, onAiAction, paintAiBar, remoteArrived } from './aiBar.js';
import { openAttachments } from './attachmentsDialog.js';
import { attachCiteCards, openCitation, pdfsOf, showCard } from './citeCard.js';
import { conflictDialog } from './conflict.js';
import { createEditor, loadMD } from './editor.js';
import { folderOfSelection, newFolder, newNote, renameNote, rescan, showTrash } from './fileOps.js';
import { refitOnResize } from './mathFit.js';
import { NoteSession } from './noteSession.js';
import { initPanels, listHidden, panelButton, peek, toggleList } from './panels.js';
import { followLink, citeTargetOf, gotoLine, initReadView, renderRead, scheduleRead } from './readView.js';
import { createRenderer, renderDefaults } from './render.js';
import { renderMermaid } from './richRender.js';
import { paintCrumbs, paintSide, paintStatus } from './sidePanel.js';
import { nKey, paintTree, refreshTree, saveCollapsed, selectOnly, setFilter, tree, treeEl } from './tree.js';
import { initTreeInput } from './treeInput.js';
import {
  citeInfo, equationMode, hooks, itemInfo, type Mode, noteMacros, normTarget, notesEnabled, notesOpen, resolveNote, stemOf, ws,
} from './workspace.js';

export { notesOpen } from './workspace.js';

ws.mode = storage<Mode>('zc.notes.mode', 'live');
ws.editMode = ws.mode === 'read' ? 'live' : ws.mode;

let opening = Promise.resolve();
let lastMathKey: string | null = null;

const drafts = {
  get: (k: string) => storage(k, null),
  set: (k: string, v: unknown) => store(k, v),
  remove: (k: string) => { try { localStorage.removeItem(k); } catch { /* storage unavailable */ } },
};

/* ================================================================== setup */
export function initNotes(): void {
  const root = h(`<div id="notes-view" hidden>
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
      <div class="note-ai-bar" role="status" hidden></div>
      <div class="note-body">
        <div class="note-editor"></div>
        <div class="note-read" tabindex="0" hidden><article class="prose"></article></div>
        <div class="note-empty empty-state">${icon('doc')}<h3>No note open</h3>
          <p>Pick a note on the left, or create one with <b>+</b> (${modLabel}N).</p></div>
      </div>
    </section>
    <aside class="notes-side" aria-label="Note details"><div class="ns-details"></div></aside>
  </div>`);
  ws.root = root;
  Object.assign(hooks, { openNote, doOpen, closeCurrent, setMode, openNotesGraph });
  document.getElementById('app')!.appendChild(root);
  root.querySelector('.notes-side')!.prepend(localSection());
  initNotesGraph({
    root,
    openNote: (p) => openNote(p),
    currentPath: () => ws.session?.path || null,
    openGraph: (focus) => openNotesGraph(focus),
    onGlobalClosed: () => navPush({ ws: 'notes', path: ws.session?.path || null }),
  });

  root.addEventListener('click', onClick);
  window.zcNotes = { text: () => ws.session?.text ?? null, path: () => ws.session?.path ?? null,
    cursor: () => ws.editor?.view.state.selection.main.head ?? null, focused: () => !!ws.editor?.view.hasFocus };
  listenForQuotes();
  initReadView();
  refitOnResize(root.querySelector<HTMLElement>('.note-read')!);
  renderDefaults.equations = equationMode;
  renderDefaults.macros = noteMacros;
  applyLayout();
  initPanels(root);
  attachCiteCards(root, { itemInfo, openNote: (p) => openNote(p), refreshLibrary: async () => { await loadLibrary(); await refreshTree(); }, targetOf: citeTargetOf });
  let libTimer: ReturnType<typeof setTimeout> | undefined;
  on('items', () => { if (notesOpen()) { clearTimeout(libTimer); libTimer = setTimeout(loadLibrary, 500); } });
  root.querySelector<HTMLInputElement>('.notes-filter input')!.addEventListener('input', (e) => {
    setFilter((e.target as HTMLInputElement).value.trim().toLowerCase());
  });
  initTreeInput();

  window.addEventListener('focus', () => { if (notesOpen()) { ws.session?.checkRemote(); refreshTree(); } });
  // Claude (the MCP server) saves from another process: look for such changes while the note is visible
  setInterval(() => { if (notesOpen() && !document.hidden && ws.session?.status === 'saved') ws.session.checkRemote(); }, 5000);
  document.addEventListener('visibilitychange', () => { if (document.hidden) ws.session?.save(); });
  window.addEventListener('pagehide', flushOnUnload);
  // Diagrams are drawn for one theme: redraw them when light/dark changes.
  new MutationObserver(() => { if (!notesOpen() || !ws.session) return; ws.editor?.refresh(); if (ws.mode === 'read') renderRead(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  on('search', () => { if (notesOpen() && state.query) closeNotes(); });   // a library search shows its results
  on('settings', () => {
    if (!notesEnabled() && notesOpen()) closeNotes();
    emit('notes-count', notesEnabled() ? ws.overview.notes.length : null);
    applyLayout();
    const key = `${equationMode()}|${state.settings.notes_math_physics}|${state.settings.notes_math_macros}`;
    if (key !== lastMathKey) { lastMathKey = key; ws.editor?.refresh(); if (ws.mode === 'read') renderRead(); }
  });

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
function listenForQuotes(): void {
  if (!('BroadcastChannel' in window)) return;
  const ch = new BroadcastChannel('zc-notes');
  ch.onmessage = (e) => {
    const m = e.data || {};
    const { session, editor } = ws;
    if (m.type === 'who' && notesOpen() && session && editor && session.status !== 'conflict') {
      ch.postMessage({ type: 'here', req: m.req, tab: TAB_ID, path: session.path, title: ws.info?.title || stemOf(session.path) });
    }
    if (m.type === 'insert' && m.tab === TAB_ID && session && editor) {
      editor.insertBlock(String(m.markdown || ''));
      if (ws.mode === 'read') renderRead();
      toast('Added from the PDF.', { type: 'success' });
      ch.postMessage({ type: 'inserted', req: m.req, tab: TAB_ID, path: session.path });
    }
  };
}

/** Last chance when the tab closes: send unsaved text (the local draft stays as a safety net). */
function flushOnUnload(): void {
  const session = ws.session;
  if (!session || !session.dirty || session.status === 'conflict') return;
  try {
    fetch('/api/notes/note', {
      method: 'PUT', keepalive: true, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: session.path, text: session.text, hash: session.saved.hash }),
    });
  } catch { /* the draft is still there */ }
}

/* ============================================================ open/close */
/** Notes changed elsewhere (another window or device): pick it up like coming back to the tab. */
export function notesChanged(): void {
  if (!notesEnabled()) return;
  if (notesOpen()) ws.session?.checkRemote();
  refreshTree();   // also the notes count in the sidebar and the pencil column
}

export async function openNotes(path: string | null = null, line: number | null = null,
  { graph = false, focus = null }: { graph?: boolean; focus?: string | null } = {}): Promise<void> {
  if (!notesEnabled()) return;
  document.body.dataset.workspace = 'notes';
  ws.root.hidden = false;
  store('zc.notes.open', true);
  emit('workspace');
  await Promise.all([refreshTree(), loadLibrary()]);
  const target = path || ws.session?.path || storage<string | null>('zc.notes.active', null);
  if (target && ws.overview.notes.some((n) => n.path === target)) {
    await openNote(target);
    if (line) gotoLine(line);
  } else {
    if (path) toast(`“${stemOf(path)}” no longer exists (moved or deleted).`);
    if (!ws.session) paintEmpty();
  }
  if (graph) await openGlobal(focus); else closeGlobal();
  const at = ws.session?.path || null;
  navPush(graph ? { ws: 'notes', path: at, graph: true, focus } : { ws: 'notes', path: at });
}

/** The full graph in Notes, optionally centred on a node ("note:<path>", "item:<id>"). */
export async function openNotesGraph(focus: string | null = null): Promise<void> {
  if (!notesEnabled()) return;
  if (!notesOpen()) await openNotes();
  await openGlobal(focus);
  navPush({ ws: 'notes', path: ws.session?.path || null, graph: true, focus });
}

/** ⌘⇧G: the graph, or back to the note. */
export async function toggleNotesGraph(): Promise<void> {
  if (notesOpen() && globalOpen()) { closeGlobal({ push: true }); ws.editor?.focus(); } else await openNotesGraph(ws.session ? `note:${ws.session.path}` : null);
}

export async function closeNotes(): Promise<void> {
  if (!notesOpen()) return;
  await ws.session?.save();
  delete document.body.dataset.workspace;
  ws.root.hidden = true;
  store('zc.notes.open', false);
  emit('workspace');
  emit('focus-list');
  navPush({ ws: 'library', view: state.view, sel: [...state.selection] });
}

/** Text width (Settings: a readable column or the whole pane). The panels: panels.ts. */
function applyLayout(): void {
  ws.root.classList.toggle('wide-text', state.settings.notes_width === 'full');
}

/** The toolbar's panel buttons while Notes is open (main.ts). */
export function notesPanelButton(a: string): void { panelButton(a); }

async function ensureEditor() {
  if (ws.editor) return ws.editor;
  const lib = await loadMD();
  ws.lib = lib;
  ws.renderer = createRenderer(lib, { citeInfo, resolveNote, equations: equationMode, macros: noteMacros });
  ws.editor = await createEditor(ws.root.querySelector<HTMLElement>('.note-editor')!, {
    onChange: (text) => { ws.session?.update(text); if (ws.mode === 'read') scheduleRead(); },
    onSave: () => ws.session?.save(),
    onBlur: () => ws.session?.save(),
    onToggleRead: () => setMode(ws.mode === 'read' ? ws.editMode : 'read'),
    onImage: async (file) => {
      try { return (await api.uploadNoteImage(file)).markdown; } catch (e) { toastError(e); return null; }
    },
  }, {
    renderer: () => ws.renderer,
    resolveNote,
    citeInfo,
    openNote: (target, heading) => followLink(target, heading),
    openCite: (key, page, target, opts) => { if (!openCitation(key, page, target, opts)) toast(`@${key} is not in your library.`); },
    mermaid: renderMermaid,
    equations: equationMode,
    macros: noteMacros,
  }, ws.mode === 'read' ? ws.editMode : ws.mode, {
    items: () => ws.library.list,
    notes: () => ws.overview.notes,
  });
  return ws.editor;
}

/* ---------------------------------------------------- link data and modes */
async function loadLibrary(): Promise<void> {
  try {
    const res = await api.citeIndex();
    ws.library = { list: res.items, byKey: new Map(res.items.map((i) => [i.key, i])) };
    ws.editor?.refresh();
  } catch { /* autocomplete stays empty until the next try */ }
}

async function loadLinks(): Promise<void> {
  if (!ws.session) return;
  const path = ws.session.path;
  try {
    const res = await api.noteLinks(path);
    if (ws.session?.path !== path) return;
    const notes = new Map<string, string | null>(), cites = new Map<string, NonNullable<typeof res.outgoing[number]['item']>>();
    for (const l of res.outgoing) {
      if (l.kind === 'note' || l.kind === 'embed') notes.set(normTarget(l.target), l.resolved || null);
      if (l.kind === 'cite' && l.item) cites.set(l.target, l.item);
    }
    ws.linkData = { notes, cites };
    ws.linkResp = { ...res, path_for: path };
    ws.editor?.refresh();
    paintSide();
    if (ws.mode === 'read') renderRead();
  } catch { /* shown as unknown until the next save */ }
}

function setMode(m: Mode, { focus = true } = {}): void {
  if (!['live', 'source', 'read'].includes(m)) m = 'live';
  ws.mode = m;
  if (m !== 'read') ws.editMode = m;
  store('zc.notes.mode', m);
  const root = ws.root;
  root.querySelectorAll<HTMLElement>('[data-mode]').forEach((b) => b.classList.toggle('active', b.dataset.mode === m));
  const read = root.querySelector<HTMLElement>('.note-read')!;
  root.classList.toggle('reading', m === 'read');
  read.hidden = m !== 'read';
  if (m === 'read') { renderRead(); if (focus) read.focus(); return; }
  ws.editor?.setMode(m);
  if (focus) ws.editor?.focus();
}

/** Open a note (saving the current one first). Calls are serialised. */
function openNote(path: string): Promise<void> {
  closeGlobal();
  opening = opening.then(() => doOpen(path)).catch((e) => { toastError(e); });
  return opening;
}

async function doOpen(path: string): Promise<void> {
  if (ws.session && ws.session.path === path) { if (!treeEl().contains(document.activeElement)) ws.editor?.focus(); return; }
  if (ws.session) {
    await ws.session.save();
    ws.session.dispose();
    ws.session = null;
  }
  const res = await api.getNote(path);
  const ed = await ensureEditor();
  const start = NoteSession.open(path, res, drafts);
  ws.linkData = { notes: new Map(), cites: new Map() };
  ws.linkResp = null;
  ed.load(start.text);
  ws.info = res.info;
  const session = new NoteSession({
    path, text: start.text, serverText: res.text, hash: res.hash, drafts,
    delay: state.settings.notes_autosave_ms || 1500,
    api: { save: api.saveNote, get: api.getNote, create: api.recreateNote },
    onStatus: paintStatus,
    onConflict: (c) => conflictDialog(c),
    onRemoteText: (t) => { ed.replace(t); if (ws.mode === 'read') renderRead(); remoteArrived(path); },
    onSaved: () => afterSave(),
  });
  ws.session = session;
  store('zc.notes.active', path);
  if (notesOpen()) navPush({ ws: 'notes', path });
  if (!tree.sel.has(nKey(path))) selectOnly(nKey(path));   // opened from elsewhere: the list follows
  ws.root.classList.add('has-note');
  showLocal(path);
  paintStatus(session.status);
  paintCrumbs();
  paintSide();
  paintTree();
  // opened from the file list: the keyboard stays there (arrows, Delete) until you click the note
  setMode(ws.mode, { focus: !treeEl().contains(document.activeElement) });
  loadLinks();
  loadAiEdits();
  if (start.conflict) await session.startConflict();
  else if (start.restored) toast('Restored changes that had not been saved yet.', { type: 'success' });
}

function paintEmpty(): void {
  const root = ws.root;
  root.classList.remove('has-note');
  showLocal(null);
  root.querySelector('.note-crumbs')!.innerHTML = '';
  root.querySelector('.note-status')!.textContent = '';
  root.querySelector('.notes-side .ns-details')!.innerHTML = '';
  ws.aiHist = null;
  paintAiBar();
}

async function closeCurrent(): Promise<void> {
  if (!ws.session) return;
  ws.session.dispose();
  ws.session = null;
  ws.info = null;
  store('zc.notes.active', null);
  paintEmpty();
  paintTree();
}

let afterSaveTimer: ReturnType<typeof setTimeout> | undefined;
function afterSave(): void {
  paintAiBar();
  // titles, folders and outline may have changed: refresh lazily
  clearTimeout(afterSaveTimer);
  afterSaveTimer = setTimeout(async () => {
    if (!ws.session) return;
    try {
      const res = await api.getNote(ws.session.path);
      if (ws.session && res.path === ws.session.path) { ws.info = res.info; paintSide(); }
    } catch { /* next save will try again */ }
    loadLinks();
    refreshTree();
    graphChanged();
  }, 400);
}

/* ================================================================ events */
/** Clicks on the head, the note bar and the side panel (the tree has its own: treeInput.ts). */
function onClick(e: MouseEvent): void {
  const t = e.target as Element;
  const { session, info } = ws;
  const actEl = t.closest<HTMLElement>('[data-act]');
  const act = actEl?.dataset.act;
  if (act === 'new-note') { newNote(); return; }
  if (act === 'new-folder') { newFolder(folderOfSelection()); return; }
  if (act === 'graph') { openNotesGraph(session ? `note:${session.path}` : null); return; }
  if (act === 'rename' && session) { renameNote(session.path); return; }
  if (act === 'show-conflict' && session?.conflict) { conflictDialog(session.conflict); return; }
  if (act?.startsWith('ai-')) { onAiAction(act, t.closest<HTMLElement>('[data-ai-id]')?.dataset.aiId); return; }
  if (act === 'paper-item' && info?.paper) { const it = itemInfo(info.paper); if (it) emit('show-item', it.id); return; }
  if (act === 'paper-pdf' && info?.paper) {
    const it = itemInfo(info.paper);
    if (!it) return;
    const pdfs = pdfsOf(it);
    if (pdfs.length > 1) openMenu(pdfs.map((p) => ({ label: `Open “${p.name || 'PDF'}”`, icon: 'pdf', run: () => openAttachment({ id: it.id, title: it.title }, p) })), actEl!);
    else if (pdfs.length) openAttachment({ id: it.id, title: it.title }, pdfs[0]);
    return;
  }
  if (act === 'tree-menu') {
    openMenu([
      { label: 'Rescan notes folder', icon: 'refresh', run: rescan },
      { label: 'Deleted notes…', icon: 'trash', run: showTrash },
      { label: 'Attachments…', icon: 'paperclip', run: () => openAttachments({
        insert: (md) => {
          const { session: s, editor } = ws;
          if (!s || !editor || s.status === 'conflict') return false;
          editor.insertBlock(md);
          if (ws.mode === 'read') renderRead();
          return true;
        },
        openNote: (p) => openNote(p),
      }) },
      'sep',
      { label: 'Collapse all folders', icon: 'minus', run: () => { ws.overview.folders.forEach((f) => tree.collapsed.add(f)); saveCollapsed(); paintTree(); } },
      { label: 'Expand all folders', icon: 'plus', run: () => { tree.collapsed.clear(); saveCollapsed(); paintTree(); } },
    ], actEl!);
  }
  const mb = t.closest<HTMLElement>('[data-mode]');
  if (mb) { setMode(mb.dataset.mode as Mode); return; }
  const op = t.closest<HTMLElement>('[data-open-path]');
  if (op) { openNotes(op.dataset.openPath, Number(op.dataset.openLine) || null); return; }
  const cn = t.closest<HTMLElement>('[data-create-note]');
  if (cn) { followLink(cn.dataset.createNote!, ''); return; }
  const cr = t.closest<HTMLElement>('.ns-row[data-cite-keys]');
  if (cr) { gotoLine(Number(cr.dataset.gotoLine)); showCard(cr); return; }
  const ol = t.closest<HTMLElement>('[data-outline-line]');
  if (ol) gotoLine(Number(ol.dataset.outlineLine), 'start');
}

/** Keyboard shortcuts while the notes workspace is open (called from main.ts). */
export function notesShortcut(e: KeyboardEvent, k: string, mod: boolean): boolean {
  const inEditor = !!(e.target as Element).closest?.('.cm-editor');
  if (mod && e.key === '\\' && !e.shiftKey && !e.altKey) { e.preventDefault(); toggleList(); return true; }
  if (mod && k === 'e' && ws.session && !inEditor) { e.preventDefault(); setMode(ws.mode === 'read' ? ws.editMode : 'read'); return true; }
  if (mod && k === 'n' && !e.shiftKey) { e.preventDefault(); newNote(); return true; }
  if (mod && k === 'f' && !inEditor) { e.preventDefault(); if (listHidden()) peek(true); ws.root.querySelector<HTMLElement>('.notes-filter input')!.focus(); return true; }
  return false;
}
