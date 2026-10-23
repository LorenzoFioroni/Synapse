// The Tasks page: the views column (views, notes), the list with its search, sort, group and
// quick add, and the detail pane of the selected task. It covers the item list and item pane like
// Notes does (body[data-workspace="tasks"]). See docs/tasks.md.
import { api } from '../../../api.js';
import { icon } from '../../../icons.js';
import { navPush } from '../../../nav.js';
import { type AppEvent, type AppEvents, emit, off, on, state } from '../../../store.js';
import type { Task } from '../../../types.js';
import { openDialog } from '../../../ui/dialog.js';
import { openMenu } from '../../../ui/menu.js';
import { toastError } from '../../../ui/toast.js';
import { escapeHtml, h, modLabel, plural, storage, store } from '../../../util.js';
import { ws } from '../../../views/notes/workspace.js';
import { dueLabel, parseQuick } from './dates.js';
import { pickNote } from './notePicker.js';
import { GROUPS, SORTS, VIEWS, type GroupId, type SortId, type ViewId, viewCounts } from './taskQuery.js';
import { createTask, loadTasks, restoreTask, td } from './taskData.js';
import { type TaskForm, mountTaskForm } from './taskForm.js';
import { type TaskList, createTaskList } from './taskList.js';
import { ensureRenderer } from './taskRender.js';
import { loadState } from './tasksState.js';

export const tasksOpen = (): boolean => document.body.dataset.workspace === 'tasks';
const enabled = () => state.settings.notes_enabled !== false;

let root: HTMLElement;
let list: TaskList;
let form: TaskForm | null = null;
const st = loadState();
let quickDest: string | null = null;   // the note quick add puts tasks in (null: none)

const offs: (() => void)[] = [];
function listen<K extends AppEvent>(event: K, fn: (payload: AppEvents[K]) => void): void {
  on(event, fn); offs.push(() => off(event, fn));
}
export function teardownTasks(): void {
  closeTasks();
  form?.destroy(); form = null;
  offs.splice(0).forEach((off) => off());
  root.remove();
  emit('tasks-count', null);
}

export function initTasks(): void {
  root = h(`<div id="tasks-view" hidden>
    <nav class="tv-views" aria-label="Task views"></nav>
    <section class="tv-main">
      <div class="tv-head">
        <div class="tv-row1">
          <h2 class="tv-heading"></h2>
          <div class="search tv-search" role="search">${icon('search')}
            <input type="search" class="tv-search-input" placeholder="Search tasks: words, due:week, is:done, note:…" aria-label="Search tasks" spellcheck="false" autocomplete="off">
            <button class="search-clear" data-tv="clear-search" title="Clear (Esc)" hidden>${icon('close', 'icon-sm')}</button>
            <button class="search-mode" data-tv="search-help" title="Search filters">Filters ${icon('chevronDown', 'icon-sm')}</button></div>
        </div>
        <div class="tv-row2">
          <div class="tv-quick">${icon('plus')}<input class="tv-quick-input" placeholder="New task… (a date at the end: fri)" spellcheck="false" aria-label="Add a task">
            <button type="button" class="tv-dest" data-tv="dest" title="Where the new task is shown"></button></div>
          <button class="btn btn-sm" data-tv="sort" title="Sort">${icon('sort')}<span></span></button>
          <button class="btn btn-sm" data-tv="group" title="Group">${icon('group')}<span></span></button>
          <button class="icon-btn tv-toggle" data-tv="done" title="Show done sub-tasks">${icon('check')}</button>
          <button class="icon-btn tv-toggle" data-tv="flat" title="Flat list (sub-tasks on their own rows)">${icon('list')}</button>
        </div>
        <div class="tv-quick-preview" hidden></div>
      </div>
      <div class="tv-list" role="tree" aria-multiselectable="true" tabindex="0" aria-label="Tasks"></div>
    </section>
    <aside class="tv-detail" aria-label="Task details"><div class="tv-detail-empty empty-state">${icon('tasks')}<h3>No task selected</h3><p>Pick a task to see its details and sub-tasks.</p></div><div class="tv-form" hidden></div><div class="tv-multi" hidden></div></aside>
  </div>`);
  document.getElementById('app')!.appendChild(root);
  list = createTaskList({
    el: root.querySelector<HTMLElement>('.tv-list')!,
    state: st,
    onSelect: paintDetail,
    repaint: paintChrome,
    focusDetail: (what) => {
      if (!form) return;
      if (what === 'title') form.focusTitle();
      else root.querySelector<HTMLInputElement>('.tf-date-input')?.focus();
    },
  });
  root.addEventListener('click', onClick);
  applyTaskPanels();
  const search = root.querySelector<HTMLInputElement>('.tv-search-input')!;
  search.addEventListener('input', () => { st.query = search.value; st.keep.clear(); root.querySelector<HTMLElement>('[data-tv="clear-search"]')!.hidden = !search.value; list.paint(); });
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); if (search.value) { search.value = ''; search.dispatchEvent(new Event('input')); } else focusList(); }
    if (e.key === 'ArrowDown' || e.key === 'Enter') { e.preventDefault(); focusList(); }
  });
  const quick = root.querySelector<HTMLInputElement>('.tv-quick-input')!;
  quick.addEventListener('input', paintQuickPreview);
  quick.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); quickAdd(); }
    if (e.key === 'Escape') { e.preventDefault(); quick.value = ''; paintQuickPreview(); focusList(); }
    if (e.key === 'ArrowDown') { e.preventDefault(); focusList(); }
  });
  quick.addEventListener('blur', () => setTimeout(paintQuickPreview, 150));
  quick.addEventListener('focus', paintQuickPreview);
  root.querySelector('.tv-views')!.addEventListener('dragover', (e) => {
    const n = (e.target as Element).closest<HTMLElement>('[data-tv-note]');
    if (n && n.dataset.tvNote && list.dragging()) { (e as DragEvent).preventDefault(); n.classList.add('drop-target'); }
  });
  root.querySelector('.tv-views')!.addEventListener('dragleave', (e) => (e.target as Element).closest?.('[data-tv-note]')?.classList.remove('drop-target'));
  root.querySelector('.tv-views')!.addEventListener('drop', async (e) => {
    const n = (e.target as Element).closest<HTMLElement>('[data-tv-note]');
    const id = list.dragging();
    n?.classList.remove('drop-target');
    if (!n?.dataset.tvNote || !id) return;
    e.preventDefault();
    try { await api.addTaskToNote(id, n.dataset.tvNote); await loadTasks(true); } catch (err) { toastError(err); }
  });

  listen('tasks', () => { if (tasksOpen()) { list.paint(); paintViews(); paintDetail(); } paintCount(); });
  listen('workspace', () => {
    // another workspace took over (Notes opened, or back to the library)
    if (!tasksOpen() && !root.hidden) { root.hidden = true; form?.destroy(); form = null; store('zc.tasks.open', false); }
  });
  listen('settings', () => { if (!enabled() && tasksOpen()) closeTasks(); paintCount(); });
  if (enabled()) loadTasks().then(() => { if (root.isConnected && storage('zc.tasks.open', false)) openTasks(); }).catch(() => {});
}

/** The sidebar's count (open tasks, overdue ones in red). */
function paintCount(): void {
  if (!td.loaded) return;
  const c = viewCounts([...td.tasks.values()], td.refs);
  emit('tasks-count', enabled() ? { open: c.open, overdue: c.overdue } : null);
}

export async function openTasks({ task = null, note = null }: { task?: string | null; note?: string | null } = {}): Promise<void> {
  if (!enabled() || !root.isConnected) return;
  const opened = root;
  document.body.dataset.workspace = 'tasks';
  root.hidden = false;
  store('zc.tasks.open', true);
  emit('workspace');
  if (note !== null) { st.note = note || null; if (!['open', 'all', 'done'].includes(st.view)) st.view = 'open'; st.save(); }
  await Promise.all([loadTasks(), ensureRenderer(), ws.library.list.length ? null : api.citeIndex().then((r) => { ws.library = { list: r.items, byKey: new Map(r.items.map((i) => [i.key, i])) }; }).catch(() => {})]);
  if (!opened.isConnected || opened !== root) return;
  if (task && td.tasks.has(task)) {
    const t = td.tasks.get(task)!;
    // make it visible: its view, its parents unfolded
    if (t.status === 'done' && st.view !== 'done' && st.view !== 'all') st.showDone = true;
    for (let p = t.parent_id; p; p = td.tasks.get(p)?.parent_id || null) st.folded.delete(p);
    if (!list.rows().length) list.paint();
    st.sel = new Set([task]);
    st.cursor = task;
  }
  list.paint();
  paintViews();
  paintDetail();
  if (task && !list.rows().some((r) => r.type === 'task' && r.task.id === task)) {
    st.view = 'all'; st.note = null; st.query = '';
    root.querySelector<HTMLInputElement>('.tv-search-input')!.value = '';
    list.paint(); paintViews();
  }
  list.paintSelection();
  focusList();
  navPush({ ws: 'tasks', task: st.cursor });
}

export function closeTasks(): void {
  if (!tasksOpen()) return;
  delete document.body.dataset.workspace;
  root.hidden = true;
  form?.destroy();
  form = null;
  store('zc.tasks.open', false);
  emit('workspace');
  emit('focus-list');
}

/** Other windows or Claude changed tasks (changes.ts). */
export function tasksChanged(): void {
  if (td.loaded || tasksOpen()) loadTasks(true).catch(() => {});
}

const focusList = () => root.querySelector<HTMLElement>('.tv-list')!.focus();

/* ------------------------------------------------------------------ painting */
function paintChrome(): void {
  const title = st.note ? (ws.overview.notes.find((n) => n.path === st.note)?.title || st.note.replace(/\.md$/, ''))
    : VIEWS[st.view].name;
  const sub = st.note ? `<span class="tv-sub">${VIEWS[st.view].name}</span>` : '';
  root.querySelector('.tv-heading')!.innerHTML = `${escapeHtml(title)}${sub}<span class="n">${list.count()}</span>`;
  root.querySelector('[data-tv="sort"] span')!.textContent = SORTS[st.sort];
  root.querySelector('[data-tv="group"] span')!.textContent = st.group === 'none' ? 'No groups' : `By ${GROUPS[st.group].toLowerCase()}`;
  root.querySelector('[data-tv="done"]')!.classList.toggle('on', st.showDone);
  root.querySelector('[data-tv="flat"]')!.classList.toggle('on', st.flat);
  const dest = quickDest ?? st.note;
  root.querySelector('[data-tv="dest"]')!.innerHTML = `${escapeHtml(dest ? (ws.overview.notes.find((n) => n.path === dest)?.title || dest) : 'No note')} ${icon('chevronDown', 'icon-sm')}`;
}

function paintViews(): void {
  const c = viewCounts([...td.tasks.values()], td.refs);
  const v = (id: ViewId) => `<button class="side-item${st.view === id && !st.note ? ' active' : ''}" data-tv-view="${id}">${icon(VIEWS[id].icon)}<span class="nm">${VIEWS[id].name}</span>
      <span class="count">${id === 'today' && c.overdue ? `<b class="overdue" title="${plural(c.overdue, 'task')} overdue">${c.overdue}</b> ` : ''}${id === 'all' ? '' : c[id] || ''}</span></button>`;
  // notes that show open tasks, by title
  const byNote = new Map<string, { title: string; n: number }>();
  for (const [id, refs] of Object.entries(td.refs)) {
    const t = td.tasks.get(id);
    if (!t) continue;
    for (const r of refs) {
      const e = byNote.get(r.path) || { title: r.title || r.path.replace(/\.md$/, ''), n: 0 };
      if (t.status !== 'done') e.n += 1 + openBelow(id);
      byNote.set(r.path, e);
    }
  }
  const notes = [...byNote.entries()].sort((a, b) => a[1].title.localeCompare(b[1].title));
  root.querySelector('.tv-views')!.innerHTML = `
    <div class="tv-views-head">Tasks<button class="icon-btn" data-tv="new" title="New task (${modLabel}N)">${icon('plus')}</button></div>
    ${(['open', 'today', 'upcoming', 'nodate', 'unfiled', 'done', 'all'] as ViewId[]).map(v).join('')}
    <div class="side-section">In notes</div>
    ${notes.length ? notes.map(([path, e]) => `<button class="side-item${st.note === path ? ' active' : ''}" data-tv-note="${escapeHtml(path)}" title="${escapeHtml(path)}">${icon('doc')}<span class="nm">${escapeHtml(e.title)}</span><span class="count">${e.n || ''}</span></button>`).join('')
      : '<div class="side-empty">Tasks you add to notes are listed by note here.</div>'}
    <div class="tv-views-foot"><button class="link" data-tv="trash">${icon('trash', 'icon-sm')} Deleted tasks${td.trash ? ` (${td.trash})` : ''}…</button></div>`;
}
function openBelow(id: string): number {
  let n = 0;
  for (const t of td.tasks.values()) if (t.parent_id === id) n += (t.status === 'done' ? 0 : 1) + openBelow(t.id);
  return n;
}

function paintDetail(): void {
  if (!tasksOpen()) return;
  const ids = [...st.sel].filter((id) => td.tasks.has(id));
  const empty = root.querySelector<HTMLElement>('.tv-detail-empty')!;
  const box = root.querySelector<HTMLElement>('.tv-form')!;
  const multi = root.querySelector<HTMLElement>('.tv-multi')!;
  empty.hidden = ids.length > 0;
  box.hidden = ids.length !== 1;
  multi.hidden = ids.length < 2;
  if (ids.length === 1) {
    form ||= mountTaskForm(box, {
      onOpenTask: (id) => { const t = td.tasks.get(id); if (t) for (let p = t.parent_id; p; p = td.tasks.get(p)?.parent_id || null) st.folded.delete(p); list.paint(); list.select(id); },
    });
    if (form.id() !== ids[0]) form.show(ids[0]);
    navPush({ ws: 'tasks', task: ids[0] });
  }
  if (ids.length > 1) {
    const ts = ids.map((id) => td.tasks.get(id)!) as Task[];
    const open = ts.filter((t) => t.status !== 'done').length;
    multi.innerHTML = `<h3>${plural(ids.length, 'task')} selected</h3>
      <p class="muted">${open} open${ts.some((t) => t.due) ? `, the first due ${escapeHtml(dueLabel(ts.filter((t) => t.due).map((t) => t.due!).sort()[0]))}` : ''}.</p>
      <div class="tv-multi-actions">
        <button class="btn" data-tv="bulk-done">${icon('check')} ${open ? 'Mark as done' : 'Mark as open'}</button>
        <button class="btn" data-tv="bulk-menu">${icon('calendar')} Due date…</button>
        <button class="btn" data-tv="bulk-note">${icon('doc')} Show in a note…</button>
        <button class="btn btn-ghost btn-danger" data-tv="bulk-delete">${icon('trash')} Delete</button></div>`;
  }
}

function paintQuickPreview(): void {
  const input = root.querySelector<HTMLInputElement>('.tv-quick-input')!;
  const box = root.querySelector<HTMLElement>('.tv-quick-preview')!;
  const v = input.value.trim();
  if (!v || document.activeElement !== input) { box.hidden = true; return; }
  const q = parseQuick(v);
  const bits = q.due ? `<span class="task-chip due">${icon('calendar')}${escapeHtml(dueLabel(q.due))}</span>` : '';
  const dest = quickDest ?? st.note;
  box.hidden = false;
  box.innerHTML = `<span class="task-check ghost"></span><span class="tv-quick-title">${escapeHtml(q.title || v)}</span>${bits}
    <span class="muted tv-quick-where">${dest ? `in ${escapeHtml(ws.overview.notes.find((n) => n.path === dest)?.title || dest)}` : 'not in a note'} · Enter adds</span>`;
}

async function quickAdd(): Promise<void> {
  const input = root.querySelector<HTMLInputElement>('.tv-quick-input')!;
  const v = input.value.trim();
  if (!v) return;
  const q = parseQuick(v);
  const dest = quickDest ?? st.note;
  input.value = '';
  paintQuickPreview();
  const t = await createTask({ title: q.title || v, due: q.due, note: dest || undefined });
  if (t) {
    list.remember({ label: 'Add', run: () => import('./taskData.js').then((m) => m.deleteTasks([t.id])) });
    if (!list.rows().some((r) => r.type === 'task' && r.task.id === t.id)) { st.view = 'open'; st.query = ''; root.querySelector<HTMLInputElement>('.tv-search-input')!.value = ''; list.paint(); paintViews(); }
    list.select(t.id);
  }
  input.focus();
}

/* ------------------------------------------------------------------ events */
async function onClick(e: Event): Promise<void> {
  const t = e.target as Element;
  const view = t.closest<HTMLElement>('[data-tv-view]');
  if (view) { st.view = view.dataset.tvView as ViewId; st.note = null; if (st.view === 'done' && st.sort === 'position') st.sort = 'done'; else if (st.view !== 'done' && st.sort === 'done') st.sort = 'position'; afterFilter(); return; }
  const note = t.closest<HTMLElement>('[data-tv-note]');
  if (note) { st.note = st.note === note.dataset.tvNote ? null : note.dataset.tvNote!; if (!['open', 'all', 'done'].includes(st.view)) st.view = 'open'; afterFilter(); return; }
  const b = t.closest<HTMLElement>('[data-tv]');
  if (!b) return;
  const a = b.dataset.tv;
  if (a === 'clear-search') { const s = root.querySelector<HTMLInputElement>('.tv-search-input')!; s.value = ''; s.dispatchEvent(new Event('input')); s.focus(); }
  if (a === 'new') root.querySelector<HTMLInputElement>('.tv-quick-input')!.focus();
  if (a === 'sort') {
    openMenu([
      ...(Object.entries(SORTS) as [SortId, string][]).map(([k, label]) => ({ label, checked: st.sort === k, run: () => { st.sort = k; afterFilter(); } })),
      'sep', { label: 'Sub-tasks stay under their parent', disabled: true },
    ], b);
  }
  if (a === 'group') openMenu((Object.entries(GROUPS) as [GroupId, string][]).map(([k, label]) => ({ label, checked: st.group === k, run: () => { st.group = k; afterFilter(); } })), b);
  if (a === 'done') { st.showDone = !st.showDone; afterFilter(); }
  if (a === 'flat') { st.flat = !st.flat; afterFilter(); }
  if (a === 'search-help') searchHelp(b);
  if (a === 'dest') destMenu(b);
  if (a === 'trash') showTrash();
  if (a === 'bulk-done') { const ids = list.selected(); const open = ids.some((id) => td.tasks.get(id)?.status !== 'done'); list.change(ids, { status: open ? 'done' : 'open' }, open ? 'Mark done' : 'Reopen'); }
  if (a === 'bulk-menu') { const id = list.selected()[0]; if (id) list.rowMenu(id, b); }
  if (a === 'bulk-note') list.showInNote(list.selected());
  if (a === 'bulk-delete') list.removeSelected();
}

function afterFilter(): void {
  st.keep.clear();
  st.save();
  list.paint();
  paintViews();
  paintDetail();
}

function searchHelp(anchor: HTMLElement): void {
  const input = root.querySelector<HTMLInputElement>('.tv-search-input')!;
  const add = (f: string) => { input.value = `${input.value.trim() ? `${input.value.trim()} ` : ''}${f}`; input.dispatchEvent(new Event('input')); input.focus(); };
  openMenu([
    { section: 'Filters (combine with words)' },
    ...[['due:today', 'Due today'], ['due:overdue', 'Overdue'], ['due:week', 'Due within a week'], ['due:none', 'Without a date'],
      ['due:<2026-12-31', 'Due before a date'], ['is:done', 'Done'], ['is:open', 'Open'],
      ['note:"title"', 'Shown in a note'], ['has:body', 'With details'], ['has:subtasks', 'With sub-tasks']].map(([f, label]) => ({ label: `${f}  —  ${label}`, run: () => add(f) })),
    { label: 'Also: "exact phrase", -exclude', disabled: true },
  ], anchor);
}

function destMenu(anchor: HTMLElement): void {
  const recent = [...new Set(Object.values(td.refs).flat().map((r) => r.path))].slice(0, 8);
  const title = (p: string) => ws.overview.notes.find((n) => n.path === p)?.title || p.replace(/\.md$/, '');
  openMenu([
    { section: 'New tasks go to' },
    { label: 'No note (only here)', checked: (quickDest ?? st.note) === null, run: () => { quickDest = null; paintChrome(); } },
    ...recent.map((p) => ({ label: title(p), checked: (quickDest ?? st.note) === p, run: () => { quickDest = p; paintChrome(); } })),
    'sep',
    { label: 'Another note…', icon: 'doc', run: async () => { const p = await pickNote('New tasks go to'); if (p) { quickDest = p; paintChrome(); } } },
  ], anchor);
}

async function showTrash(): Promise<void> {
  let tasks: Task[] = [];
  try { tasks = (await api.taskTrash()).tasks; } catch (e) { toastError(e); return; }
  const top = tasks.filter((t) => !t.parent_id || !tasks.some((x) => x.id === t.parent_id));
  await openDialog({
    title: 'Deleted tasks',
    subtitle: 'They are deleted for good after the notes trash retention (Settings → Notes).',
    render: (body) => {
      body.innerHTML = top.length ? `<div class="tv-trash">${top.map((t) => `<div class="tv-trash-row"><span class="task-check${t.status === 'done' ? ' done' : ''}"></span>
          <span class="nm">${escapeHtml(t.title)}</span><span class="muted">${escapeHtml(new Date(t.deleted_at || '').toLocaleDateString())}</span>
          <button class="btn btn-sm" data-restore="${t.id}">Restore</button></div>`).join('')}</div>` : '<p class="muted">The trash is empty.</p>';
      body.addEventListener('click', async (e) => {
        const b = (e.target as Element).closest<HTMLElement>('[data-restore]');
        if (!b) return;
        await restoreTask(b.dataset.restore!);
        b.closest('.tv-trash-row')?.remove();
      });
    },
    buttons: [{ label: 'Close', kind: 'primary' }],
  });
}

/** Keyboard shortcuts while the Tasks page is open (main.ts). */
export function tasksShortcut(e: KeyboardEvent, k: string, mod: boolean): boolean {
  if (mod && k === 'n' && !e.shiftKey) { e.preventDefault(); root.querySelector<HTMLInputElement>('.tv-quick-input')!.focus(); return true; }
  if (mod && k === 'f') { e.preventDefault(); const s = root.querySelector<HTMLInputElement>('.tv-search-input')!; s.focus(); s.select(); return true; }
  return false;
}

/** The toolbar's panel buttons while Tasks is open: the views column and the detail pane. */
export function tasksPanelButton(a: string): void {
  const cls = a === 'pane' ? 'tv-detail-hidden' : 'tv-views-hidden';
  root.classList.toggle(cls);
  store(`zc.tasks.${cls}`, root.classList.contains(cls));
}
export function applyTaskPanels(): void {
  for (const cls of ['tv-detail-hidden', 'tv-views-hidden']) root.classList.toggle(cls, storage(`zc.tasks.${cls}`, false));
}
