// The Notes tab on a phone: recent notes and folders, search; a note opens for reading (citations
// as chips, a tap on one shows what it cites) and Edit turns it into the editor, with a bar of
// Markdown helpers above the keyboard (plugins add their own buttons there).
import { api, type ApiError } from '../api.js';
import { editorButtons } from '../extend.js';
import { icon } from '../icons.js';
import { off, on, state } from '../store.js';
import type { CiteEntry, NoteInfo } from '../types.js';
import { confirmDialog, promptDialog } from '../ui/dialog.js';
import { openMenu } from '../ui/menu.js';
import { toast, toastError } from '../ui/toast.js';
import { copyText, debounce, escapeHtml as esc, storage, store } from '../util.js';
import { pickFiles } from '../views/itemlist/menus.js';
import { citeTargetInDom, viewerTarget } from '../views/notes/citeTarget.js';
import { type CM, createEditor, loadCM, loadMD, type NoteEditor } from '../views/notes/editor.js';
import { toggleFormat } from '../views/notes/formatting.js';
import { noteHooks, ghostSource } from '../views/notes/hooks.js';
import { linkDataOf } from '../views/notes/linkData.js';
import { splitFrontmatter } from '../views/notes/mdscan.js';
import { NoteSession, type SaveStatus } from '../views/notes/noteSession.js';
import { createRenderer, imageSrc } from '../views/notes/render.js';
import { highlightCodeBlocks, renderEmbeds, renderMermaid, renderMermaidBlocks } from '../views/notes/richRender.js';
import { labelId } from '../views/notes/equations.js';
import { fitEquations, fitSoon } from '../views/notes/mathFit.js';
import { citeInfo, equationMode, folderColor, itemInfo, noteMacros, parentOf, resolveNote, stemOf, taskHooks, ws } from '../views/notes/workspace.js';
import { familyNames, shortWhen } from './model.js';
import { readerScreen } from './reader.js';
import { type Screen, back, push, selectTab } from './shell.js';
import { chevron, navBar, navButton, openSheet, rootHeader, roundButton, settingsButton } from './ui.js';

const drafts = {
  get: (k: string) => storage(k, null),
  set: (k: string, v: unknown) => store(k, v),
  remove: (k: string) => { try { localStorage.removeItem(k); } catch { /* storage unavailable */ } },
};

/* ---------------------------------------------------------------- shared data */
let libraryLoaded = 0;
/** The notes listing and the citable papers, fetched when needed (and again after changes). */
async function ensureIndexes({ fresh = false } = {}): Promise<void> {
  const jobs: Promise<void>[] = [];
  if (fresh || !ws.overview.notes.length) jobs.push(api.notes().then((o) => { ws.overview = o; }).catch(() => {}));
  if (fresh || Date.now() - libraryLoaded > 60_000) {
    jobs.push(api.citeIndex().then((r) => { ws.library = { list: r.items, byKey: new Map(r.items.map((i) => [i.key, i])) }; libraryLoaded = Date.now(); }).catch(() => {}));
  }
  await Promise.all(jobs);
}
on('items', () => { libraryLoaded = 0; });

/** markdown-it and KaTeX for notes (and the shared renderer), loaded once. */
export async function ensureNoteLib(): Promise<void> {
  if (ws.lib) return;
  ws.lib = await loadMD();
  ws.renderer = createRenderer(ws.lib, { citeInfo, resolveNote, equations: equationMode, macros: noteMacros });
}

const titleOf = (n: Pick<NoteInfo, 'title' | 'path'>) => n.title || stemOf(n.path);
const dot = (path: string) => { const c = folderColor(path); return c ? `<span class="ph-dot" style="background:${esc(c)}"></span>` : ''; };

/* ---------------------------------------------------------------- the list */
export function notesScreen(): Screen {
  return {
    title: 'Notes',
    key: 'notes',
    mount(el) {
      el.innerHTML = `<div class="ph-scroll">
        ${rootHeader('Notes', { actions: `${roundButton('edit', 'New note', 'data-act="new"')}${settingsButton()}` })}
        <label class="ph-search">${icon('search')}<input type="search" placeholder="Search notes" aria-label="Search notes" autocomplete="off" spellcheck="false"></label>
        <div class="ph-notes-body"></div></div>`;
      const box = el.querySelector<HTMLElement>('.ph-notes-body')!;
      const input = el.querySelector<HTMLInputElement>('input')!;
      const paint = () => {
        if (input.value.trim()) return;
        const notes = [...ws.overview.notes].sort((a, b) => b.mtime - a.mtime).slice(0, 12);
        const top = ws.overview.folders.filter((f) => !f.includes('/') && !f.startsWith('.')).sort((a, b) => a.localeCompare(b));
        box.innerHTML = notes.length || top.length ? `
          ${notes.length ? `<h2 class="ph-section-label">Recent</h2><div class="ph-group">${notes.map(noteRow).join('')}</div>` : ''}
          ${top.length ? `<h2 class="ph-section-label">Folders</h2><div class="ph-group">${top.map(folderRow).join('')}</div>` : ''}`
          : `<div class="ph-empty">${icon('edit')}<h3>No notes yet</h3><p>Write one with the pencil above, or the + below.</p></div>`;
      };
      const search = debounce(async () => {
        const q = input.value.trim();
        if (!q) { paint(); return; }
        try {
          const res = await api.searchNotes(q);
          if (input.value.trim() !== q) return;
          box.innerHTML = res.results.length ? `<div class="ph-group">${res.results.map((r) => `<button class="ph-note-row" data-note="${esc(r.path)}">
            <span class="ph-row-title">${esc(r.title || stemOf(r.path))}</span><span class="ph-row-snippet">${esc(plain(r.snippet))}</span>
            <span class="ph-row-meta">${dot(r.path)}${esc(parentOf(r.path) || 'Notes')}</span></button>`).join('')}</div>`
            : `<div class="ph-empty"><h3>Nothing found</h3></div>`;
        } catch (e) { toastError(e); }
      }, 250);
      input.addEventListener('input', search);
      el.addEventListener('click', (e) => {
        const t = e.target as Element;
        const n = t.closest<HTMLElement>('[data-note]');
        if (n) { push(noteScreen(n.dataset.note!)); return; }
        const f = t.closest<HTMLElement>('[data-folder]');
        if (f) { push(folderScreen(f.dataset.folder!)); return; }
        const act = t.closest<HTMLElement>('[data-act]')?.dataset.act;
        if (act === 'new') void newNote();
        if (act === 'settings') void import('./main.js').then((m) => m.settingsMenu());
      });
      const refresh = () => paint();
      on('notes-count', refresh);
      paint();
      void ensureIndexes({ fresh: true }).then(paint);
      return () => off('notes-count', refresh);
    },
  };
}

/** A search snippet as plain text (the server marks the matching words). */
function plain(html: string): string {
  return new DOMParser().parseFromString(html, 'text/html').body.textContent || '';
}

function noteRow(n: NoteInfo): string {
  return `<button class="ph-note-row" data-note="${esc(n.path)}">
    <span class="ph-row-title-line"><span class="ph-row-title">${esc(titleOf(n))}</span><span class="ph-when">${esc(shortWhen(n.mtime))}</span></span>
    <span class="ph-row-meta">${dot(n.path)}${esc(parentOf(n.path) || 'Notes')}${n.type === 'paper' ? ' · paper note' : ''}</span></button>`;
}

function folderRow(f: string): string {
  const n = ws.overview.notes.filter((x) => x.path.startsWith(`${f}/`)).length;
  const c = folderColor(f);
  return `<button class="ph-folder-row" data-folder="${esc(f)}"><span class="ph-folder-icon"${c ? ` style="color:${esc(c)}"` : ''}>${icon('folder')}</span>
    <span class="grow">${esc(f.split('/').pop()!)}</span><span class="ph-count">${n}</span>${chevron()}</button>`;
}

function folderScreen(folder: string): Screen {
  const name = folder.split('/').pop()!;
  return {
    title: name,
    key: `notes-folder:${folder}`,
    mount(el, ctx) {
      const paint = () => {
        const subs = ws.overview.folders.filter((f) => parentOf(f) === folder).sort((a, b) => a.localeCompare(b));
        const notes = ws.overview.notes.filter((n) => parentOf(n.path) === folder).sort((a, b) => titleOf(a).localeCompare(titleOf(b)));
        el.innerHTML = `${navBar(ctx.backLabel, name, navButton('edit', 'New note here', 'data-act="new"'))}<div class="ph-scroll">
          ${subs.length ? `<div class="ph-group">${subs.map(folderRow).join('')}</div>` : ''}
          ${notes.length ? `<div class="ph-group">${notes.map(noteRow).join('')}</div>` : ''}
          ${!subs.length && !notes.length ? '<div class="ph-empty"><p>This folder is empty.</p></div>' : ''}</div>`;
      };
      paint();
      el.addEventListener('click', (e) => {
        const t = e.target as Element;
        const n = t.closest<HTMLElement>('[data-note]');
        if (n) { push(noteScreen(n.dataset.note!)); return; }
        const f = t.closest<HTMLElement>('[data-folder]');
        if (f) { push(folderScreen(f.dataset.folder!)); return; }
        if (t.closest('[data-act="new"]')) void newNote(folder);
      });
      on('notes-count', paint);
      return () => off('notes-count', paint);
    },
  };
}

/** Ask for a title and open the new note, ready to write. */
export async function newNote(folder = ''): Promise<void> {
  const title = await promptDialog('Title', { title: 'New note', placeholder: 'Untitled', confirm: 'Create', hint: folder ? `In ${folder}/` : '' });
  if (title === null) return;
  try {
    const res = await api.createNote({ title: title || 'Untitled', folder });
    await ensureIndexes({ fresh: true });
    if (!folder) selectTab('notes');
    push(noteScreen(res.path, { edit: true }));
  } catch (e) { toastError(e); }
}

/* ---------------------------------------------------------------- a note */
const STATUS: Record<SaveStatus, string> = { saved: 'Saved', dirty: 'Edited', saving: 'Saving…', offline: 'Offline — kept here', conflict: 'Changed elsewhere' };

export function noteScreen(path: string, { line = null, edit = false }: { line?: number | null; edit?: boolean } = {}): Screen {
  return {
    title: stemOf(path),
    key: `note:${path}`,
    async mount(el, ctx) {
      el.classList.add('ph-note-screen');
      el.innerHTML = `${navBar(ctx.backLabel, '', `<span class="ph-save" aria-live="polite"></span>${navButton('more', 'More', 'data-act="more"')}<button class="ph-pill accent" data-act="edit">Edit</button>`)}
        <div class="ph-scroll ph-note"><div class="ph-loading">Loading…</div></div>
        <div class="ph-note-edit note-editor" hidden></div>
        <div class="ph-keybar" hidden role="toolbar" aria-label="Writing tools"></div>`;
      const scroll = el.querySelector<HTMLElement>('.ph-note')!;
      const editBox = el.querySelector<HTMLElement>('.ph-note-edit')!;
      const keybar = el.querySelector<HTMLElement>('.ph-keybar')!;
      const editBtn = el.querySelector<HTMLButtonElement>('[data-act="edit"]')!;
      let res;
      try {
        [res] = await Promise.all([api.getNote(path), ensureNoteLib(), ensureIndexes()]);
      } catch (e) {
        scroll.innerHTML = `<div class="ph-empty">${icon('alert')}<h3>Note not found</h3><p>${esc((e as ApiError).message)}</p></div>`;
        editBtn.hidden = true;
        return;
      }
      const start = NoteSession.open(path, res, drafts);
      let linkData = ws.linkData;
      let editor: NoteEditor | null = null;
      let editing = false;
      const saveLabel = el.querySelector<HTMLElement>('.ph-save')!;
      const session = new NoteSession({
        path, text: start.text, serverText: res.text, hash: res.hash, drafts,
        delay: state.settings.notes_autosave_ms || 1500,
        api: { save: api.saveNote, get: api.getNote, check: api.checkNote, create: api.recreateNote },
        onStatus: (st) => { saveLabel.textContent = editing || st !== 'saved' ? STATUS[st] : ''; saveLabel.dataset.status = st; },
        onConflict: async (c) => {
          // the phone keeps it simple: keep what you wrote (saved over), or take the other version
          const keep = await confirmDialog(c.deleted ? 'This note was deleted elsewhere. Keep your text as the note?' : 'This note was changed elsewhere while you wrote.',
            { title: 'Changed elsewhere', confirm: 'Keep mine', detail: c.deleted ? '' : 'Cancel takes the other version instead; what you wrote here is then lost.' });
          try { await session.resolve(keep || c.deleted ? 'mine' : 'theirs'); } catch (e) { toastError(e); }
        },
        onRemoteText: (t) => { editor?.replace(t); if (!editing) render(); },
        onSaved: () => { void loadLinks(); taskHooks.saved?.(path, session.text); },
        onMoved: () => false,
      });
      /** This note's parts in the workspace the shared notes code reads (citations, links, tasks). */
      const activate = () => {
        ws.linkData = linkData;
        ws.session = session;
        ws.editor = editor;
      };
      const loadLinks = async () => {
        try { linkData = linkDataOf(await api.noteLinks(path)); activate(); if (!editing) render(); else editor?.refresh(); } catch { /* links show as unknown */ }
      };

      const render = () => {
        activate();
        const text = session.text.replace(/\r\n?/g, '\n');
        const fm = splitFrontmatter(text);
        const r = createRenderer(ws.lib!, { citeInfo, resolveNote, lineOffset: fm.bodyLine, equations: equationMode, macros: noteMacros });
        const info = ws.overview.notes.find((n) => n.path === path);
        scroll.innerHTML = `<div class="ph-note-meta">${dot(path)}${esc(parentOf(path) || 'Notes')}${info ? ` · edited ${esc(shortWhen(info.mtime))}` : ''}</div>
          <article class="prose ph-prose">${r.render(text.slice(fm.bodyStart))}</article>`;
        const art = scroll.querySelector<HTMLElement>('article')!;
        // wide equations shrink to fit (down to ¾), the rest scrolls sideways
        if (art.querySelector('.katex-display')) { fitEquations(art); fitSoon(art); }
        void highlightCodeBlocks(art);
        void renderMermaidBlocks(art);
        void renderEmbeds(art, { citeInfo, resolveNote, equations: equationMode, macros: noteMacros }, new Set([path]));
      };

      const ensureEditor = async (): Promise<NoteEditor> => {
        if (editor) return editor;
        editor = await createEditor(editBox, {
          onChange: (t) => session.update(t),
          onSave: () => session.save(),
          onBlur: () => session.save(),
          onModEnter: (v) => taskHooks.key?.(v) ?? false,
          onImage: async (file) => { try { return (await api.uploadNoteImage(file)).markdown; } catch (e) { toastError(e); return null; } },
          combineImages: (images) => (noteHooks.figures && images.length > 1 ? noteHooks.figures.gallery(images, {}) : images.join('\n')),
        }, {
          renderer: () => ws.renderer, resolveNote, citeInfo,
          openNote: (target) => { const p = resolveNote(target); if (p) push(noteScreen(p)); },
          openCite: (key, page) => citeSheet(key, page, null),
          mermaid: renderMermaid, equations: equationMode, macros: noteMacros,
          task: () => taskHooks.preview,
        }, 'live', {
          items: () => ws.library.list,
          notes: () => ws.overview.notes,
          tasks: () => taskHooks.choices?.() ?? null,
          newTask: (title) => taskHooks.create?.(title) ?? Promise.resolve(null),
          taskHtml: (id) => taskHooks.titleHtml?.(id) ?? '',
          assist: ghostSource,
        });
        editor.load(session.text);
        for (const fn of noteHooks.editorOpened) { try { fn(); } catch (e) { console.error(e); } }
        activate();
        return editor;
      };

      const keyboard = () => {
        // keep the bar just above the on-screen keyboard
        const vv = window.visualViewport;
        const kb = vv ? Math.max(0, innerHeight - vv.height - vv.offsetTop) : 0;
        el.style.setProperty('--kb', `${kb}px`);
      };
      const setEditing = async (on: boolean, at: number | null = null) => {
        if (on === editing) return;
        if (on) {
          const ed = await ensureEditor();
          editing = true;
          el.classList.add('editing');
          scroll.hidden = true; editBox.hidden = false; keybar.hidden = false;
          paintKeybar(keybar);
          editBtn.textContent = 'Done';
          saveLabel.textContent = STATUS[session.status];
          if (at !== null) ed.gotoLine(at + 1);
          ed.focus();
          window.visualViewport?.addEventListener('resize', keyboard);
          window.visualViewport?.addEventListener('scroll', keyboard);
          keyboard();
        } else {
          editing = false;
          el.classList.remove('editing');
          editor?.view.contentDOM.blur();
          void session.save();
          scroll.hidden = false; editBox.hidden = true; keybar.hidden = true;
          editBtn.textContent = 'Edit';
          saveLabel.textContent = session.status === 'saved' ? '' : STATUS[session.status];
          window.visualViewport?.removeEventListener('resize', keyboard);
          window.visualViewport?.removeEventListener('scroll', keyboard);
          render();
        }
      };

      render();
      void loadLinks();
      if (start.restored) toast(start.conflict ? 'Your unsaved changes were kept; the note also changed elsewhere.' : 'Your unsaved changes were brought back.');
      if (line !== null) requestAnimationFrame(() => scroll.querySelector(`[data-line="${line}"]`)?.scrollIntoView({ block: 'center' }));
      if (edit) void setEditing(true);

      el.addEventListener('click', (e) => {
        const t = e.target as Element;
        const act = t.closest<HTMLElement>('[data-act]')?.dataset.act;
        if (act === 'edit') { void setEditing(!editing); return; }
        if (act === 'more') { noteMenu(path, session); return; }
        const kb = t.closest<HTMLElement>('[data-key]');
        if (kb && editor) { e.preventDefault(); void keyAction(kb.dataset.key!, editor); return; }
        if (editing || !scroll.contains(t)) return;
        onReadClick(e, () => ensureEditor(), render);
      });
      // keep the editor focused when a bar button is pressed (the keyboard stays up)
      keybar.addEventListener('mousedown', (e) => e.preventDefault());
      keybar.addEventListener('touchstart', () => {}, { passive: true });
      // a double tap on a paragraph edits it there
      scroll.addEventListener('dblclick', (e) => {
        if ((e.target as Element).closest('a, button, .cite, .task-ref, input')) return;
        const block = (e.target as Element).closest<HTMLElement>('[data-line]');
        void setEditing(true, block ? Number(block.dataset.line) : null);
      });
      ctx.onLeave(async () => { await session.save(); });
      // the notes changed elsewhere (another device, an AI app): pick up this one's new text
      const remote = () => { void session.checkRemote(); void loadLinks(); };
      on('notes-count', remote);
      return () => {
        off('notes-count', remote);
        void session.save().finally(() => session.dispose());
        editor?.destroy();
        window.visualViewport?.removeEventListener('resize', keyboard);
        window.visualViewport?.removeEventListener('scroll', keyboard);
        if (ws.session === session) { ws.session = null; ws.editor = null; }
      };
    },
  };
}

/** A tap in a note being read: citations, links to notes, equation references, checkboxes. */
function onReadClick(e: MouseEvent, editor: () => Promise<NoteEditor>, render: () => void): void {
  const t = e.target as Element;
  const cite = t.closest<HTMLElement>('.cite');
  if (cite) { e.preventDefault(); citeSheet(cite.dataset.key!, cite.dataset.page ? Number(cite.dataset.page) : null, cite); return; }
  const wl = t.closest<HTMLElement>('.wikilink');
  if (wl) {
    e.preventDefault();
    const p = resolveNote(wl.dataset.note || '');
    if (p) push(noteScreen(p)); else toast(`There is no note “${wl.dataset.note}” yet.`);
    return;
  }
  const ref = t.closest<HTMLElement>('.eqref[data-label]');
  if (ref) {
    e.preventDefault();
    document.getElementById(labelId(ref.dataset.label!))?.parentElement?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    return;
  }
  const box = t.closest<HTMLElement>('.task-box[data-task-line]');
  if (box) {
    e.preventDefault();
    void editor().then((ed) => { if (ed.toggleTask(Number(box.dataset.taskLine))) render(); });
    return;
  }
  const a = t.closest('a');
  const href = a?.getAttribute('href');
  if (a && href && !href.startsWith('#')) { e.preventDefault(); window.open(a.href, '_blank', 'noopener'); }
}

/** What a citation points at, as a sheet: open the PDF there, the paper note, the paper. */
function citeSheet(key: string, page: number | null, el: HTMLElement | null): void {
  const it: CiteEntry | null = itemInfo(key);
  const sheet = openSheet('Citation');
  if (!it) { sheet.body.innerHTML = `<p class="ph-muted">@${esc(key)} is not in your library.</p>`; return; }
  const pdf = it.pdf;
  sheet.body.innerHTML = `
    <p class="ph-kicker">${page ? `Cited at page ${page} · ` : ''}${esc(key)}${it.trashed ? ' · in the Trash' : ''}</p>
    <h3 class="ph-sheet-title">${esc(it.title || 'Untitled')}</h3>
    <p class="ph-muted">${esc([familyNames(it.authors), it.journal, it.year ? String(it.year) : ''].filter(Boolean).join(' · '))}</p>
    ${pdf ? `<button class="ph-btn primary wide" data-c="pdf">${icon('pdf')}${page ? `Open the PDF at page ${page}` : 'Open the PDF'}</button>` : ''}
    <div class="ph-group inset">
      ${it.note ? `<button class="ph-link-row" data-c="note">${icon('edit')}<span>Paper note</span>${chevron()}</button>` : ''}
      <button class="ph-link-row" data-c="item">${icon('library')}<span>Show in library</span>${chevron()}</button>
      <button class="ph-link-row" data-c="copy">${icon('copy')}<span>Copy citation key</span></button>
    </div>`;
  sheet.body.addEventListener('click', async (e) => {
    const c = (e.target as Element).closest<HTMLElement>('[data-c]')?.dataset.c;
    if (!c) return;
    if (c === 'copy') { if (await copyText(key)) toast(`Copied ${key}`, { type: 'success', timeout: 2000 }); return; }
    sheet.close();
    if (c === 'note' && it.note) push(noteScreen(it.note));
    if (c === 'item') { const { itemScreen } = await import('./library.js'); push(itemScreen(it.id)); }
    if (c === 'pdf' && pdf) {
      const at = await viewerTarget(el ? citeTargetInDom(el) : null, { imageUrl: imageSrc }).catch(() => ({} as { q?: string; rect?: string; page?: number }));
      push(readerScreen({ id: it.id, title: it.title, authors: it.authors, year: it.year ?? undefined }, pdf, page || at.page || null, { q: at.q, rect: at.rect }));
    }
  });
}

/** The bar above the keyboard: Markdown helpers, then what plugins add. */
function paintKeybar(bar: HTMLElement): void {
  const btn = (key: string, label: string, title: string, cls = '') => `<button class="ph-key ${cls}" data-key="${esc(key)}" aria-label="${esc(title)}" title="${esc(title)}">${label}</button>`;
  bar.innerHTML = `<div class="ph-keys">
    ${btn('cite', '[@ <span>Cite</span>', 'Cite a paper', 'wide')}
    ${btn('link', '[[ <span>Link</span>', 'Link a note', 'wide')}
    ${btn('math', '<span class="ph-math">∑</span>', 'Math')}
    ${btn('image', icon('image'), 'Insert an image')}
    ${editorButtons.all().map((b) => btn(`plugin:${b.id}`, b.label ? esc(b.label) : icon(b.icon || 'plugin'), b.title)).join('')}
    ${btn('format', icon('more'), 'More formatting')}
  </div>${btn('hide', icon('chevronDown'), 'Hide the keyboard', 'hide')}`;
}

async function keyAction(key: string, editor: NoteEditor): Promise<void> {
  const view = editor.view;
  const cm: CM = await loadCM();
  const type = (text: string, close = '', complete = false) => {
    const { from, to } = view.state.selection.main;
    view.dispatch({ changes: { from, to, insert: text + close }, selection: { anchor: from + text.length }, userEvent: 'input.type' });
    view.focus();
    if (complete) cm.startCompletion(view);
  };
  const atLineStart = (prefix: string) => {
    const line = view.state.doc.lineAt(view.state.selection.main.head);
    view.dispatch({ changes: { from: line.from, insert: prefix }, userEvent: 'input' });
    view.focus();
  };
  if (key.startsWith('plugin:')) { editorButtons.get(key.slice(7))?.run(view); return; }
  switch (key) {
    case 'cite': type('[@', ']', true); break;
    case 'link': type('[[', ']]', true); break;
    case 'math': type('$', '$'); break;
    case 'image': pickFiles(async (files) => {
      const md: string[] = [];
      for (const f of [...files]) { try { md.push((await api.uploadNoteImage(f)).markdown); } catch (e) { toastError(e); } }
      if (md.length) editor.insertBlock(md.length > 1 && noteHooks.figures ? noteHooks.figures.gallery(md, {}) : md.join('\n'));
    }, 'image/*'); break;
    case 'hide': view.contentDOM.blur(); break;
    case 'format': openMenu([
      { label: 'Heading', icon: 'text', run: () => atLineStart('## ') },
      { label: 'Bulleted list', icon: 'list', run: () => atLineStart('- ') },
      { label: 'Checkbox', icon: 'check', run: () => atLineStart('- [ ] ') },
      { label: 'Quote', icon: 'text', run: () => atLineStart('> ') },
      { label: 'Bold', icon: 'text', run: () => { toggleFormat(cm, 'bold'); view.focus(); } },
      { label: 'Italic', icon: 'text', run: () => { toggleFormat(cm, 'italic'); view.focus(); } },
      { label: 'Equation on its own line', icon: 'text', run: () => type('\n$$\n', '\n$$\n') },
      'sep',
      { label: 'Undo', icon: 'restore', run: () => { cm.undo(view); view.focus(); } },
      { label: 'Redo', icon: 'refresh', run: () => { cm.redo(view); view.focus(); } },
    ], { x: 0, y: innerHeight }); break;
  }
}

/** A note's ⋯ menu: rename, copy its link, delete. */
function noteMenu(path: string, session: NoteSession): void {
  openMenu([
    { label: 'Rename…', icon: 'edit', run: async () => {
      const name = await promptDialog('Name', { title: 'Rename note', value: stemOf(path), confirm: 'Rename' });
      if (!name || name === stemOf(path)) return;
      await session.save();
      const dir = parentOf(path);
      try {
        const res = await api.moveNote(path, `${dir ? `${dir}/` : ''}${name}.md`, session.saved.hash);
        await ensureIndexes({ fresh: true });
        back();
        push(noteScreen(res.path));
      } catch (e) { toastError(e); }
    } },
    { label: 'Copy Link', icon: 'link', run: async () => { if (await copyText(`[[${stemOf(path)}]]`)) toast('Link copied', { type: 'success', timeout: 2000 }); } },
    'sep',
    { label: 'Delete Note', icon: 'trash', danger: true, run: async () => {
      await session.save();
      try {
        const res = await api.trashNotes([path]);
        session.dispose();
        await ensureIndexes({ fresh: true });
        back();
        toast('Note deleted', { action: { label: 'Undo', run: async () => { await api.restoreNoteBatch(res.trash_id); await ensureIndexes({ fresh: true }); } } });
      } catch (e) { toastError(e); }
    } },
  ], { x: 0, y: innerHeight });
}
