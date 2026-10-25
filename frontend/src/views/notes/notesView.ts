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
import { loadAiEdits, onAiAction, paintAiBar, remoteArrived } from './aiBar.js';
import { ghostSource, noteHooks, onNoteHookChange } from './hooks.js';
import { openAttachments } from './attachmentsDialog.js';
import { attachCiteCards, openCitation, pdfsOf, showCard } from './citeCard.js';
import { conflictDialog } from './conflict.js';
import { createEditor, loadMD, type NoteEditor } from './editor.js';
import { exportMenu } from './exportNote.js';
import { folderOfSelection, newFolder, newNote, renameNote, rescan, showTrash } from './fileOps.js';
import { linkDataOf } from './linkData.js';
import { refitOnResize } from './mathFit.js';
import { NoteSession } from './noteSession.js';
import { initPanels, listHidden, panelButton, peek, toggleList } from './panels.js';
import { followLink, citeTargetOf, gotoLine, initReadView, readTopLine, renderRead, scheduleRead, scrollReadTo } from './readView.js';
import { createRenderer, renderDefaults } from './render.js';
import { renderMermaid } from './richRender.js';
import { paintCrumbs, paintSide, paintStatus } from './sidePanel.js';
import { nKey, paintTree, refreshTree, saveCollapsed, selectOnly, setFilter, tree, treeEl } from './tree.js';
import { initTreeInput } from './treeInput.js';
import {
  citeInfo, equationMode, hooks, itemInfo, type Mode, noteMacros, noteTabs, type NoteView, notesEnabled, notesOpen, resolveNote, stemOf, taskHooks, ws,
} from './workspace.js';

export { notesOpen } from './workspace.js';

ws.mode = storage<Mode>('zc.notes.mode', 'live');   // the mode new tabs start with; each tab has its own
ws.editMode = ws.mode === 'read' ? 'live' : ws.mode;

let opening = Promise.resolve();
let lastMathKey: string | null = null;

const PARKED_MAX = 20;   // notes put away (no view) whose undo history is kept

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
        <button class="btn btn-sm note-add-task" hidden data-act="add-task" title="Add a task to this note (${modLabel}↩)">${icon('tasks', 'icon-sm')} Task</button>
        <button class="icon-btn" data-act="export" title="Export as PDF, HTML or Markdown">${icon('export')}</button>
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
  Object.assign(hooks, { openNote, doOpen, closeCurrent, setMode, openNotesGraph, saveAll, renameViews, dropViews });
  document.getElementById('app')!.appendChild(root);
  attachGraph();
  onNoteHookChange((key) => {
    if (key === 'graph') attachGraph();
    // galleries and figures (the Figures plugin) were turned on or off: draw the notes again
    if (key === 'tasks') {
      root.querySelector<HTMLElement>('.note-add-task')!.hidden = !taskHooks.add;
      if (ws.session) paintSide();
    }
    if (key === 'extended' || key === 'figures' || key === 'tasks') { staleOthers(); if (!notesOpen() || !ws.session) return; ws.editor?.refresh(); if (ws.mode === 'read') renderRead({ fresh: true }); }
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
  document.addEventListener('visibilitychange', () => { if (document.hidden) saveAll(); });
  window.addEventListener('pagehide', flushOnUnload);
  // Diagrams are drawn for one theme: redraw them when light/dark changes.
  new MutationObserver(() => { staleOthers(); if (!notesOpen() || !ws.session) return; ws.editor?.refresh(); if (ws.mode === 'read') renderRead({ fresh: true }); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  on('search', () => { if (notesOpen() && state.query) closeNotes(); });   // a library search shows its results
  on('settings', () => {
    if (!notesEnabled() && notesOpen()) closeNotes();
    emit('notes-count', notesEnabled() ? ws.overview.notes.length : null);
    applyLayout();
    const key = `${equationMode()}|${state.settings.notes_math_physics}|${state.settings.notes_math_macros}`;
    if (key !== lastMathKey) { lastMathKey = key; staleOthers(); ws.editor?.refresh(); if (ws.mode === 'read') renderRead({ fresh: true }); }
  });

  // Which note is shown comes from the tabs (a reload shows the window's note tab again).
  if (notesEnabled()) refreshTree();
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
  for (const { session } of ws.views.values()) {
    if (!session.dirty || session.status === 'conflict') continue;
    try {
      fetch('/api/notes/note', {
        method: 'PUT', keepalive: true, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: session.path, text: session.text, hash: session.saved.hash }),
      });
    } catch { /* the draft is still there */ }
  }
}

/* ============================================================ open/close */
/** Notes changed elsewhere (another window or device): pick it up like coming back to the tab. */
export function notesChanged(): void {
  if (!notesEnabled()) return;
  if (notesOpen()) ws.session?.checkRemote();
  refreshTree();   // also the notes count in the sidebar and the pencil column
}

/** Put the graph (the Graph plugin, through hooks.ts) into the notes view, or take its button
    away when the plugin is off. The plugin removes its own parts when it stops. */
function attachGraph(): void {
  const g = noteHooks.graph;
  if (!ws.root) return;   // Notes not built yet: initNotes calls this
  ws.root.querySelectorAll<HTMLElement>('[data-act="graph"]').forEach((b) => { b.hidden = !g; });
  if (!g) return;
  g.attach({
    root: ws.root,
    openNote: (p) => openNote(p),
    currentPath: () => ws.session?.path || null,
    openGraph: (focus) => openNotesGraph(focus),
    onGlobalClosed: () => navPush({ ws: 'notes', path: ws.session?.path || null }),
  });
  ws.root.querySelector('.notes-side')!.prepend(g.localSection());
  g.showLocal(ws.session?.path || null);
}

/**
 * Notes, at ``path`` (and ``line``) in a note tab: its own tab, else the preview tab, else a new one
 * (``keep``: a kept tab). Without a path: the note tab used last, else Notes with nothing open.
 */
export async function openNotes(path: string | null = null, line: number | null = null,
  { graph = false, focus = null, keep = false }: { graph?: boolean; focus?: string | null; keep?: boolean } = {}): Promise<void> {
  if (!notesEnabled()) return;
  if (path && !ws.overview.notes.some((n) => n.path === path)) await refreshTree();
  if (noteTabs.open) await noteTabs.open(path, { keep, last: !path });
  else await showNote(path);
  if (line && path && ws.session?.path === path) gotoLine(line);
  if (graph && noteHooks.graph) await noteHooks.graph.openGlobal(focus); else noteHooks.graph?.closeGlobal();
  const at = ws.session?.path || null;
  navPush(graph && noteHooks.graph ? { ws: 'notes', path: at, graph: true, focus } : { ws: 'notes', path: at });
}

/** Into the notes workspace (from the library or Tasks). Already there, the notes list is
 *  refreshed in the background: going from one note tab to another waits for nothing. */
async function enterNotes(): Promise<void> {
  const entering = !notesOpen();
  document.body.dataset.workspace = 'notes';
  ws.root.hidden = false;
  if (entering) { emit('workspace'); await Promise.all([refreshTree(), loadLibrary()]); } else refreshTree();
}

/**
 * What a note tab shows (tabs/tabs.ts through main.ts): ``path`` in the notes workspace, or Notes
 * with nothing open (null). Resolves false when the note no longer exists.
 */
export async function showNote(path: string | null, mode?: Mode): Promise<boolean> {
  if (!notesEnabled()) return false;
  await enterNotes();
  const exists = () => ws.overview.notes.some((n) => n.path === path);
  if (path && !ws.views.has(path) && !exists()) await refreshTree();
  if (path && !ws.views.has(path) && !exists()) {
    toast(`“${stemOf(path)}” no longer exists (moved or deleted).`);
    return false;
  }
  noteHooks.graph?.closeGlobal();
  if (path) await openInPlace(path, mode);
  else { await opening; showNothing(); }
  return true;
}

/** A note tab closed (tabs/tabs.ts): save its note and close its view. */
export async function closeNoteTab(path: string): Promise<void> {
  await opening;
  const view = ws.views.get(path);
  if (!view) return;
  await view.session.save();
  if (ws.view === view) await closeCurrent();
  else dropView(view);
}

/** A note tab is going to another window: save what was typed, so that window loads it. */
export function flushNote(path: string): void {
  ws.views.get(path)?.session.save();
}

/** The full graph in Notes, optionally centred on a node ("note:<path>", "item:<id>"). */
export async function openNotesGraph(focus: string | null = null): Promise<void> {
  if (!notesEnabled() || !noteHooks.graph) return;
  if (!notesOpen()) { await openNotes(null, null, { graph: true, focus }); return; }
  await noteHooks.graph.openGlobal(focus);
  navPush({ ws: 'notes', path: ws.session?.path || null, graph: true, focus });
}

/** ⌘⇧G: the graph, or back to the note. */
export async function toggleNotesGraph(): Promise<void> {
  if (notesOpen() && noteHooks.graph?.globalOpen()) { noteHooks.graph.closeGlobal({ push: true }); ws.editor?.focus(); } else await openNotesGraph(ws.session ? `note:${ws.session.path}` : null);
}

/** Leave Notes for the library (or, with ``toLibrary: false``, for another workspace that takes over). */
export async function closeNotes({ toLibrary = true }: { toLibrary?: boolean } = {}): Promise<void> {
  if (!notesOpen()) return;
  await ws.session?.save();
  delete document.body.dataset.workspace;
  ws.root.hidden = true;
  if (!toLibrary) return;
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

/* ============================================================ note views */
// Each note open in a tab has a view of its own: its session (saving), its editor, what it links to
// and the mode it is shown in. The shown view is in ws (ws.session, ws.editor…); the others are
// kept out of the page, so going back to a tab is only putting its editor back: nothing is fetched
// or drawn anew. Only
// the LIVE_VIEWS used last stay alive; an older one is put away (its editor state in ws.parked,
// which brings back the undo history when its tab is shown again).
const LIVE_VIEWS = 8;
let viewTick = 0;

/** markdown-it and KaTeX, once. */
async function ensureLib(): Promise<void> {
  if (ws.lib) return;
  const lib = await loadMD();
  ws.lib = lib;
  ws.renderer = createRenderer(lib, { citeInfo, resolveNote, equations: equationMode, macros: noteMacros });
}

const defaultMode = (): Mode => storage<Mode>('zc.notes.mode', 'live');

/** An editor for ``view``: its handlers act on that view's note, shown or not. */
async function newEditor(view: NoteView, mode: 'live' | 'source'): Promise<NoteEditor> {
  const ed = await createEditor(view.el, {
    onChange: (text) => { view.session?.update(text); if (ws.view === view && ws.mode === 'read') scheduleRead(); },
    onEdit: () => { if (ws.view === view) noteTabs.edited?.(); },
    onSave: () => view.session?.save(),
    onBlur: () => view.session?.save(),
    onToggleRead: () => setMode(ws.mode === 'read' ? ws.editMode : 'read'),
    onModEnter: (v) => taskHooks.key?.(v) ?? false,
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
    task: () => taskHooks.preview,
  }, mode, {
    items: () => ws.library.list,
    notes: () => ws.overview.notes,
    tasks: () => taskHooks.choices?.() ?? null,
    newTask: (title) => taskHooks.create?.(title) ?? Promise.resolve(null),
    taskHtml: (id) => taskHooks.titleHtml?.(id) ?? '',
    assist: ghostSource,
  });
  for (const fn of noteHooks.editorOpened) { try { fn(); } catch (e) { console.error(e); } }
  return ed;
}

/** A view for ``path``: the note fetched, an editor made (with the undo history it had, if it
 *  was put away), and its session. Not shown yet. */
async function createView(path: string, mode: Mode): Promise<NoteView> {
  const res = await api.getNote(path);
  await ensureLib();
  const el = document.createElement('div');
  el.className = 'note-editor-view';   // in the page only while shown
  const editMode = mode === 'read' ? (defaultMode() === 'read' ? 'live' : defaultMode() as Exclude<Mode, 'read'>) : mode;
  const view: NoteView = {
    path, el, session: null as unknown as NoteSession, editor: null as unknown as NoteEditor,
    info: res.info, linkData: { notes: new Map(), cites: new Map() }, linkResp: null, aiHist: null,
    mode, editMode, editorMode: editMode, top: 1, read: null, stale: false, conflict: null, used: ++viewTick,
  };
  view.editor = await newEditor(view, editMode);
  const start = NoteSession.open(path, res, drafts);
  const parked = ws.parked.get(path);
  ws.parked.delete(path);
  const resumed = !!parked && view.editor.resume(parked.state, start.text);
  if (!resumed) view.editor.load(start.text);
  if (resumed) view.top = parked!.top;
  view.session = new NoteSession({
    path, text: start.text, serverText: res.text, hash: res.hash, drafts,
    delay: state.settings.notes_autosave_ms || 1500,
    api: { save: api.saveNote, get: api.getNote, create: api.recreateNote },
    onStatus: (st) => { if (ws.view === view) paintStatus(st); },
    onConflict: (c) => { if (ws.view === view) conflictDialog(c); else view.conflict = c; },
    onRemoteText: (t) => {
      view.editor.replace(t);
      if (ws.view !== view) { view.read = null; return; }
      if (ws.mode === 'read') renderRead();
      remoteArrived(path);
    },
    onSaved: () => afterSave(view),
  });
  ws.views.set(path, view);
  if (start.conflict) view.pendingStart = 'conflict';
  else if (start.restored) view.pendingStart = 'restored';
  return view;
}

/** The shown view goes behind: what it shows is kept with it. */
function hideView(view: NoteView): void {
  view.info = ws.info;
  view.linkData = ws.linkData;
  view.linkResp = ws.linkResp;
  view.aiHist = ws.aiHist;
  view.mode = ws.mode;
  view.editMode = ws.editMode;
  if (ws.mode === 'read') {
    const art = ws.root.querySelector<HTMLElement>('.note-read .prose')!;
    view.top = readTopLine();
    const nodes = document.createDocumentFragment();
    nodes.append(...art.childNodes);
    view.read = { nodes, text: view.session.text, links: ws.linkResp };
  } else {
    view.top = view.editor.topLine();
  }
  view.el.remove();   // out of the page: one editor in the page at a time (CodeMirror allows moving it)
  view.session.save();
  ws.view = null;
}

/** Show ``view`` (in ``mode``, else the mode it had). */
function showView(view: NoteView, mode?: Mode): void {
  view.used = ++viewTick;
  Object.assign(ws, {
    view, session: view.session, editor: view.editor, info: view.info,
    linkData: view.linkData, linkResp: view.linkResp, aiHist: view.aiHist,
  });
  if (mode && mode !== view.mode) { view.mode = mode; if (mode !== 'read') view.editMode = mode; }
  ws.mode = view.mode;
  ws.editMode = view.editMode;
  ws.root.querySelector('.note-editor')!.appendChild(view.el);
  if (view.stale) { view.stale = false; view.editor.refresh(); }
  store('zc.notes.active', view.path);
  if (notesOpen()) navPush({ ws: 'notes', path: view.path });
  if (!tree.sel.has(nKey(view.path))) selectOnly(nKey(view.path));   // opened from elsewhere: the list follows
  ws.root.classList.add('has-note');
  noteHooks.graph?.showLocal(view.path);
  paintStatus(view.session.status);
  paintCrumbs();
  paintSide();
  paintTree();
  paintAiBar();
  // opened from the file list: the keyboard stays there (arrows, Delete) until you click the note
  applyMode(view, { focus: !treeEl().contains(document.activeElement) });
  if (view.conflict) { const c = view.conflict; view.conflict = null; conflictDialog(c); }
}

/** Put the view's mode on screen: its read view (as it was left, when nothing changed since) or
 *  its editor in live or source mode, at the line it was left at. */
function applyMode(view: NoteView, { focus = true } = {}): void {
  const root = ws.root, m = view.mode;
  root.querySelectorAll<HTMLElement>('[data-mode]').forEach((b) => b.classList.toggle('active', b.dataset.mode === m));
  const read = root.querySelector<HTMLElement>('.note-read')!;
  root.classList.toggle('reading', m === 'read');
  read.hidden = m !== 'read';
  if (m === 'read') {
    const art = read.querySelector<HTMLElement>('.prose')!;
    const kept = view.read;
    view.read = null;
    if (kept && kept.text === view.session.text && kept.links === ws.linkResp) art.replaceChildren(kept.nodes);
    else renderRead({ fresh: true });
    scrollReadTo(view.top);
    if (focus) read.focus();
    return;
  }
  if (view.editorMode !== m) { view.editor.setMode(m); view.editorMode = m; }
  view.editor.scrollToLine(view.top);
  if (focus) view.editor.focus();
}

/** Close a view for good (its tab closed, its note gone): nothing is kept. */
function dropView(view: NoteView): void {
  if (ws.view === view) Object.assign(ws, { view: null, session: null, editor: null, info: null, aiHist: null });
  view.session.dispose();
  view.editor.destroy();
  view.el.remove();
  ws.views.delete(view.path);
}

/** Keep LIVE_VIEWS views alive: older hidden ones are put away (saved; undo history parked). */
function evictViews(): void {
  const hidden = [...ws.views.values()].filter((v) => v !== ws.view && !v.session.dirty).sort((a, b) => a.used - b.used);
  while (ws.views.size > LIVE_VIEWS && hidden.length) {
    const v = hidden.shift()!;
    ws.parked.delete(v.path);
    ws.parked.set(v.path, { state: v.editor.park(), top: v.top });
    while (ws.parked.size > PARKED_MAX) ws.parked.delete(ws.parked.keys().next().value!);
    dropView(v);
  }
}

/** Save every open note (before moving notes or folders, and when the page goes away). */
async function saveAll(): Promise<void> {
  await Promise.all([...ws.views.values()].map((v) => v.session.save()));
}

/** Notes were renamed or moved: hidden views of them are put away under their new path (the shown
 *  one is reopened by fileOps.ts). */
function renameViews(rename: (path: string) => string): void {
  for (const v of [...ws.views.values()]) {
    const to = rename(v.path);
    if (to === v.path || v === ws.view) continue;
    ws.parked.set(to, { state: v.editor.park(), top: v.top });
    dropView(v);
  }
}

/** Notes went to the trash: their views close. */
function dropViews(gone: (path: string) => boolean): void {
  for (const v of [...ws.views.values()]) if (gone(v.path) && v !== ws.view) dropView(v);
}

/** Other views drew their preview with what they knew then: they draw again when shown. */
function staleOthers(): void {
  for (const v of ws.views.values()) if (v !== ws.view) { v.stale = true; v.read = null; }
}

/* ---------------------------------------------------- link data and modes */
async function loadLibrary(): Promise<void> {
  try {
    const res = await api.citeIndex();
    ws.library = { list: res.items, byKey: new Map(res.items.map((i) => [i.key, i])) };
    ws.editor?.refresh();
    staleOthers();
  } catch { /* autocomplete stays empty until the next try */ }
}

async function loadLinks(): Promise<void> {
  const view = ws.view;
  if (!view) return;
  try {
    const res = await api.noteLinks(view.path);
    const linkData = linkDataOf(res), linkResp = { ...res, path_for: view.path };
    if (ws.view !== view) { Object.assign(view, { linkData, linkResp, stale: true, read: null }); return; }
    ws.linkData = linkData;
    ws.linkResp = linkResp;
    ws.editor?.refresh();
    paintSide();
    if (ws.mode === 'read') renderRead();
  } catch { /* shown as unknown until the next save */ }
}

/** Show the note in another mode. ``keepPlace``: the line at the top stays there (false when a
 *  note was just opened: it starts at its top). The mode belongs to the note's tab. */
function setMode(m: Mode, { focus = true, keepPlace = true } = {}): void {
  if (!['live', 'source', 'read'].includes(m)) m = 'live';
  const view = ws.view;
  const at = !keepPlace ? 1 : ws.mode === 'read' ? readTopLine() : ws.editor?.topLine() ?? 1;
  ws.mode = m;
  if (m !== 'read') ws.editMode = m;
  store('zc.notes.mode', m);   // what new tabs start with
  if (!view) return;
  view.mode = m;
  view.editMode = ws.editMode;
  view.top = at;
  view.read = null;
  applyMode(view, { focus });
  noteTabs.modeChanged?.(m);
}

/** Open a note: in a tab (the preview tab unless ``keep``), which shows it here. */
function openNote(path: string, { keep = false }: { keep?: boolean } = {}): Promise<void> {
  if (noteTabs.open) return noteTabs.open(path, { keep });
  return openInPlace(path);
}

/** Show a note in the workspace. Calls are serialised. */
function openInPlace(path: string, mode?: Mode): Promise<void> {
  noteHooks.graph?.closeGlobal();
  opening = opening.then(() => doOpen(path, mode)).catch((e) => { toastError(e); });
  return opening;
}

async function doOpen(path: string, mode?: Mode): Promise<void> {
  const cur = ws.view;
  if (cur && cur.path === path) {
    if (mode && mode !== ws.mode) setMode(mode, { focus: false });
    if (!treeEl().contains(document.activeElement)) ws.editor?.focus();
    return;
  }
  if (cur) hideView(cur);
  let view = ws.views.get(path);
  const fresh = !view;
  if (!view) view = await createView(path, mode || defaultMode());
  else view.session.checkRemote();   // changed elsewhere while its tab was in the background?
  showView(view, mode);
  if (fresh) {
    loadLinks();
    loadAiEdits();
    if (view.pendingStart === 'conflict') await view.session.startConflict();
    else if (view.pendingStart === 'restored') toast('Restored changes that had not been saved yet.', { type: 'success' });
    view.pendingStart = undefined;
  }
  evictViews();
}

function paintEmpty(): void {
  const root = ws.root;
  root.classList.remove('has-note', 'reading');
  root.querySelector<HTMLElement>('.note-read')!.hidden = true;
  noteHooks.graph?.showLocal(null);
  root.querySelector('.note-crumbs')!.innerHTML = '';
  root.querySelector('.note-status')!.textContent = '';
  root.querySelector('.notes-side .ns-details')!.innerHTML = '';
  ws.aiHist = null;
  paintAiBar();
}

/** Nothing shown (an empty Notes tab): the view shown goes behind, still open in its tab. */
function showNothing(): void {
  if (ws.view) hideView(ws.view);
  Object.assign(ws, { session: null, editor: null, info: null, aiHist: null });
  paintEmpty();
  paintTree();
}

/** Close the note shown for good (moved, deleted, or its tab closed). */
async function closeCurrent(): Promise<void> {
  const view = ws.view;
  if (!view) return;
  dropView(view);
  ws.info = null;
  store('zc.notes.active', null);
  paintEmpty();
  paintTree();
}

let afterSaveTimer: ReturnType<typeof setTimeout> | undefined;
function afterSave(view: NoteView): void {
  taskHooks.saved?.(view.path, view.session.text);
  if (ws.view !== view) { refreshTree(); return; }
  paintAiBar();
  // titles, folders and outline may have changed: refresh lazily
  clearTimeout(afterSaveTimer);
  afterSaveTimer = setTimeout(async () => {
    if (ws.view !== view) return;
    try {
      const res = await api.getNote(view.path);
      if (ws.view === view && res.path === view.path) { ws.info = res.info; paintSide(); }
    } catch { /* next save will try again */ }
    loadLinks();
    refreshTree();
    noteHooks.graph?.changed();
  }, 400);
}

/* ================================================================ events */
/** Clicks on the head, the note bar and the side panel (the tree has its own: treeInput.ts). */
function onClick(e: MouseEvent): void {
  const t = e.target as Element;
  const { session, info } = ws;
  if (taskHooks.click?.(e)) return;
  const actEl = t.closest<HTMLElement>('[data-act]');
  const act = actEl?.dataset.act;
  if (act === 'add-task' && session) { taskHooks.add?.(actEl!); return; }
  if (act === 'new-note') { newNote(); return; }
  if (act === 'new-folder') { newFolder(folderOfSelection()); return; }
  if (act === 'graph') { openNotesGraph(session ? `note:${session.path}` : null); return; }
  if (act === 'rename' && session) { renameNote(session.path); return; }
  if (act === 'export' && session) { exportMenu(session.path, actEl!); return; }
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
