// The tasks the page knows (loaded once from /api/tasks, then kept up to date), and the changes
// the Tasks page and the notes make to them. Everyone who draws a task listens for the 'tasks'
// event. A change is applied here first, so a click shows at once; the server's answer then
// replaces it (and an error puts back what was there).
import { api, offline } from './api.js';
import { emit } from '../../../frontend/src/plugins/task-runtime.js';
import type { Task, TaskChanges, TaskRef } from '../../../frontend/src/plugins/task-runtime.js';
import { openDialog } from '../../../frontend/src/plugins/task-runtime.js';
import { toast, toastError } from '../../../frontend/src/plugins/task-runtime.js';
import { escapeHtml, plural } from '../../../frontend/src/plugins/task-runtime.js';

export const td = {
  tasks: new Map<string, Task>(),
  refs: {} as Record<string, TaskRef[]>,
  trash: 0,
  loaded: false,
  version: 0,   // goes up with every change: widgets that draw tasks use it in their keys
};

let loading: Promise<void> | null = null;
let active = false;
let generation = 0;
export function startTaskData(): void { active = true; generation++; td.loaded = false; }
export function stopTaskData(): void {
  active = false; generation++; loading = null;
  td.tasks.clear(); td.refs = {}; td.loaded = false; td.trash = 0; td.version++;
}

/** Load all tasks (once; ``force`` loads again, e.g. after another window changed them). */
export function loadTasks(force = false): Promise<void> {
  if (!active) return Promise.resolve();
  const current = generation;
  if (td.loaded && !force) return Promise.resolve();
  if (loading) return loading;
  loading = Promise.all([api.tasks(), offline.pending()]).then(([res, pending]) => {
    if (!active || current !== generation) return;
    td.tasks = new Map(res.tasks.map((t) => [t.id, t]));
    td.refs = res.refs;
    td.trash = res.trash;
    applyPending(pending);
    td.loaded = true;
    changed();
  }).finally(() => { if (current === generation) loading = null; });
  return loading;
}

/** Changes made offline that wait to go in, shown over the saved list (plugin API 1.16): new tasks,
 *  edits and moves, deletions (and their Undo), tasks put into notes or taken out. */
function applyPending(pending: Awaited<ReturnType<typeof offline.pending>>): void {
  const binned = new Map<string, Task[]>();   // a deletion made offline → its tasks, for its Undo
  for (const w of pending) {
    const body = (w.body || {}) as TaskChanges & { id?: string; base?: unknown; ids?: string[]; batch?: string; path?: string };
    if (w.method === 'POST' && w.path === '/' && body.id) {
      if (!td.tasks.has(body.id)) td.tasks.set(body.id, offlineTask(body as TaskChanges & { id: string }));
      if (body.note) addRef(body.id, body.note);
      continue;
    }
    if (w.method === 'POST' && w.path === '/delete' && body.batch) {
      const gone = subtrees(body.ids || []);
      binned.set(body.batch, gone);
      for (const t of gone) td.tasks.delete(t.id);
      td.trash += gone.length;
      continue;
    }
    if (w.method === 'POST' && w.path === '/restore') {
      // a batch, or one task with its sub-tasks of the same batch (deleted offline: shown again)
      const id = (body as { id?: string }).id;
      const batch = body.batch || [...binned].find(([, ts]) => ts.some((t) => t.id === id))?.[0];
      const ts = (batch && binned.get(batch)) || [];
      const back = body.batch ? ts : ts.filter((t) => t.id === id);
      for (let i = 0; !body.batch && i < back.length; i++) back.push(...ts.filter((t) => t.parent_id === back[i].id));
      for (const t of back) td.tasks.set(t.id, t);
      td.trash = Math.max(0, td.trash - back.length);
      continue;
    }
    const note = w.method === 'POST' ? /^\/([^/]+)\/notes(\/remove)?$/.exec(w.path) : null;
    if (note && body.path) {
      if (note[2]) td.refs[note[1]] = (td.refs[note[1]] || []).filter((r) => r.path !== body.path);
      else addRef(note[1], body.path);
      continue;
    }
    const m = w.method === 'PATCH' ? /^\/([^/]+)$/.exec(w.path) : null;
    const t = m && td.tasks.get(m[1]);
    if (t) td.tasks.set(t.id, moved(withFields(t, body), body));
  }
}

/** Shown in a note (offline: at its end, as the server puts it). */
function addRef(id: string, path: string): void {
  const refs = td.refs[id] || [];
  if (!refs.some((r) => r.path === path)) td.refs[id] = [...refs, { path, title: path.replace(/^.*\//, '').replace(/\.md$/, ''), line: 1e6, heading: null }];
}

/** The tasks and all their sub-tasks (each once), as the server deletes them. */
function subtrees(ids: string[]): Task[] {
  const out = new Map<string, Task>();
  const add = (id: string) => {
    const t = td.tasks.get(id);
    if (!t || out.has(id)) return;
    out.set(id, t);
    for (const k of allTasks()) if (k.parent_id === id) add(k.id);
  };
  ids.forEach(add);
  return [...out.values()];
}

/** ``t`` moved as ``changes`` say (under ``parent_id``, before or after a sibling), as the server places it. */
function moved(t: Task, c: TaskChanges & { before?: string | null; after?: string | null }): Task {
  if (!('parent_id' in c || 'before' in c || 'after' in c)) return t;
  const parent = c.parent_id ?? null;
  const sibs = allTasks().filter((x) => x.parent_id === parent && x.id !== t.id).sort((a, b) => a.position - b.position);
  const anchor = c.before || c.after;
  const i = anchor ? sibs.findIndex((x) => x.id === anchor) : -1;
  let position: number;
  if (i < 0) position = (sibs.length ? sibs[sibs.length - 1].position : 0) + 1;
  else if (c.after) position = (sibs[i].position + (i + 1 < sibs.length ? sibs[i + 1].position : sibs[i].position + 2)) / 2;
  else position = ((i > 0 ? sibs[i - 1].position : sibs[i].position - 2) + sibs[i].position) / 2;
  return { ...t, parent_id: parent, position };
}

/** Whether ``parent`` is ``id`` or one of its sub-tasks (a task cannot go there). */
function under(parent: string | null | undefined, id: string): boolean {
  for (let p = parent ? td.tasks.get(parent) : undefined; p; p = p.parent_id ? td.tasks.get(p.parent_id) : undefined) if (p.id === id) return true;
  return false;
}

/** ``t`` with the fields of a change applied. */
function withFields(t: Task, changes: TaskChanges): Task {
  const local: Task = { ...t };
  for (const k of ['title', 'body', 'status', 'due'] as const) if (k in changes) (local as any)[k] = changes[k];
  if ('parent_id' in changes) local.parent_id = changes.parent_id ?? null;
  return local;
}

/** A task made on this device while Synapse could not be reached, as the server will store it. */
function offlineTask(d: TaskChanges & { id: string }): Task {
  const now = new Date().toISOString();
  const siblings = allTasks().filter((t) => t.parent_id === (d.parent_id ?? null));
  return {
    id: d.id, parent_id: d.parent_id ?? null, position: siblings.reduce((n, t) => Math.max(n, t.position), 0) + 1,
    title: d.title || '', body: d.body || '', status: d.status || 'open', due: d.due ?? null,
    created_at: now, updated_at: now, done_at: d.status === 'done' ? now : null, created_by: 'human', updated_by: 'human',
    deleted_at: null, deleted_batch: null,
  };
}

/** A new task's id, chosen here so a task made offline keeps it (as the server makes them: backend/tasks/store.py). */
export function newTaskId(): string {
  const chars = 'abcdefghjkmnpqrstuvwxyz23456789';
  const r = crypto.getRandomValues(new Uint32Array(8));
  return chars[r[0] % 23] + [...r.slice(1)].map((n) => chars[n % chars.length]).join('');
}

function changed(): void {
  td.version++;
  emit('tasks');
}

export const allTasks = (): Task[] => [...td.tasks.values()];
export const taskById = (id: string): Task | undefined => td.tasks.get(id);
export function childrenOf(id: string): Task[] {
  return allTasks().filter((t) => t.parent_id === id).sort((a, b) => a.position - b.position);
}
/** Done and total sub-tasks (direct ones). */
export function progress(id: string): { done: number; total: number } {
  const kids = childrenOf(id);
  return { done: kids.filter((k) => k.status === 'done').length, total: kids.length };
}
function openBelow(id: string): number {
  return childrenOf(id).reduce((n, k) => n + (k.status === 'done' ? 0 : 1) + openBelow(k.id), 0);
}

function apply(list: Task[]): void {
  for (const t of list) td.tasks.set(t.id, t);
}

/** Change a task. Returns it as the server stored it (null when that failed; the error is shown). */
export async function patchTask(id: string, changes: TaskChanges): Promise<Task | null> {
  const before = td.tasks.get(id);
  const fields = !('parent_id' in changes || 'before' in changes || 'after' in changes);
  if (!fields && under(changes.parent_id, id)) { toast('A task cannot be moved under itself or one of its sub-tasks.', { type: 'error' }); return null; }
  const local = before ? moved(withFields(before, changes), changes) : null;
  if (local) { td.tasks.set(id, local); changed(); }
  // it may wait offline; ``base`` lets the server merge a change of fields with what changed meanwhile
  const base = before && fields ? Object.fromEntries((['title', 'body', 'status', 'due'] as const).filter((k) => k in changes).map((k) => [k, before[k]])) : undefined;
  try {
    const res = await api.updateTask(id, base ? { ...changes, base } : changes, local ? () => ({ task: local, changed: [] }) : undefined);
    apply(res.changed);
    td.tasks.set(id, res.task);
    if ('parent_id' in changes || 'before' in changes || 'after' in changes) await loadTasks(true);   // positions of the siblings
    else changed();
    return res.task;
  } catch (e) {
    if (before) td.tasks.set(id, before);
    changed();
    toastError(e);
    return null;
  }
}

/** Tick or untick a task. Ticking one whose sub-tasks are open asks whether to tick them too. */
export async function toggleTask(id: string): Promise<void> {
  const t = td.tasks.get(id);
  if (!t) return;
  if (t.status === 'done') { await patchTask(id, { status: 'open' }); return; }
  const open = openBelow(id);
  let all = false;
  if (open) {
    const choice = await openDialog({
      title: `Also mark ${plural(open, 'open sub-task')} as done?`,
      render: (b) => { b.innerHTML = `<p style="margin:0">“${escapeHtml(plainTitle(t.title))}” has ${plural(open, 'sub-task')} that ${open === 1 ? 'is' : 'are'} not done yet.</p>`; },
      buttons: [{ label: 'Cancel', value: null }, { label: 'Only this one', value: 'one' }, { label: 'Mark all', kind: 'primary', value: 'all' }],
    });
    if (choice !== 'one' && choice !== 'all') return;
    all = choice === 'all';
  }
  await patchTask(id, { status: 'done', with_subtasks: all });
}

export async function createTask(data: TaskChanges): Promise<Task | null> {
  try {
    // A new task may be made offline: it gets its id here, and waits (one for a note goes into it then).
    const own = { ...data, id: newTaskId() };
    const res = await api.createTask(own, () => {
      if (own.note) addRef(own.id, own.note);
      return { task: offlineTask(own), ...(own.note ? { ref: { path: own.note, line: 1e6 } } : {}) };
    });
    td.tasks.set(res.task.id, res.task);
    if (res.ref) await loadTasks(true); else changed();
    return res.task;
  } catch (e) { toastError(e); return null; }
}

/** Trash tasks (with their sub-tasks); the toast offers Undo. */
/** To the trash, with an Undo toast (`message` instead of the usual one; `onUndo` after Undo). */
export async function deleteTasks(ids: string[], o: { message?: string; onUndo?: () => void } = {}): Promise<void> {
  if (!ids.length) return;
  const name = ids.length === 1 ? td.tasks.get(ids[0])?.title : null;
  try {
    const res = await trashTasks(ids);
    for (const id of res.ids) td.tasks.delete(id);
    changed();
    const first = name ? `“${plainTitle(name)}”` : plural(ids.length, 'task');
    const extra = res.ids.length - ids.length;
    toast(o.message ?? `Deleted ${first}${extra > 0 ? ` and ${plural(extra, 'sub-task')}` : ''}.`, {
      action: { label: 'Undo', run: () => { o.onUndo?.(); return restoreBatch(res.batch); } }, timeout: 8000,
    });
  } catch (e) { toastError(e); }
}

/** Tasks (with their sub-tasks) to the trash, as one batch: named here, so its Undo also works offline. */
export async function trashTasks(ids: string[]): Promise<{ batch: string; ids: string[] }> {
  const batch = `${new Date().toISOString().replace(/\D/g, '').slice(0, 14)}-${[...crypto.getRandomValues(new Uint8Array(3))].map((b) => b.toString(16).padStart(2, '0')).join('')}`;
  const res = await api.deleteTasks(ids, batch, () => ({ batch, ids: subtrees(ids).map((t) => t.id) }));
  td.trash += res.ids.length;
  return res;
}

export async function restoreBatch(batch: string): Promise<void> {
  try { await api.restoreTasks(batch, () => ({ ids: [] })); await loadTasks(true); } catch (e) { toastError(e); }
}

/** Show a task in a note, or stop showing it there (offline: done in the note when Synapse answers). */
export async function addToNote(id: string, path: string): Promise<void> {
  await api.addTaskToNote(id, path, null, () => { addRef(id, path); return { ref: { path, line: 1e6 } }; });
  await loadTasks(true);
}
export async function removeFromNote(id: string, path: string): Promise<void> {
  await api.removeTaskFromNote(id, path, () => ({ path, removed: 1 }));
  await loadTasks(true);
}
export async function restoreTask(id: string): Promise<void> {
  try { await api.restoreTask(id, () => ({ ids: [id] })); await loadTasks(true); } catch (e) { toastError(e); }
}

/** A title without its Markdown, for messages and dialogs. */
export function plainTitle(title: string): string {
  return title.replace(/\$([^$]+)\$/g, '$1').replace(/[*_`]/g, '').replace(/\[@([^\]]+)\]/g, '@$1').replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_m, a, b) => b || a);
}

/** The tasks a note shows, in order (from the index, as of its last save). */
export function tasksOfNote(path: string): { task: Task | undefined; id: string; line: number }[] {
  const out: { task: Task | undefined; id: string; line: number }[] = [];
  for (const [id, refs] of Object.entries(td.refs)) {
    for (const r of refs) if (r.path === path) out.push({ id, task: td.tasks.get(id), line: r.line });
  }
  return out.sort((a, b) => a.line - b.line);
}
