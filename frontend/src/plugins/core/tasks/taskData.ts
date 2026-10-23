// The tasks the page knows (loaded once from /api/tasks, then kept up to date), and the changes
// the Tasks page and the notes make to them. Everyone who draws a task listens for the 'tasks'
// event. A change is applied here first, so a click shows at once; the server's answer then
// replaces it (and an error puts back what was there).
import { api } from '../../../api.js';
import { emit } from '../../../store.js';
import type { Task, TaskChanges, TaskRef } from '../../../types.js';
import { openDialog } from '../../../ui/dialog.js';
import { toast, toastError } from '../../../ui/toast.js';
import { escapeHtml, plural } from '../../../util.js';

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
  loading = api.tasks().then((res) => {
    if (!active || current !== generation) return;
    td.tasks = new Map(res.tasks.map((t) => [t.id, t]));
    td.refs = res.refs;
    td.trash = res.trash;
    td.loaded = true;
    changed();
  }).finally(() => { if (current === generation) loading = null; });
  return loading;
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
  if (before) {
    const local: Task = { ...before };
    for (const k of ['title', 'body', 'status', 'due'] as const) if (k in changes) (local as any)[k] = changes[k];
    if ('parent_id' in changes) local.parent_id = changes.parent_id ?? null;
    td.tasks.set(id, local);
    changed();
  }
  try {
    const res = await api.updateTask(id, changes);
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
    const res = await api.createTask(data);
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
    const res = await api.deleteTasks(ids);
    for (const id of res.ids) td.tasks.delete(id);
    td.trash += res.ids.length;
    changed();
    const first = name ? `“${plainTitle(name)}”` : plural(ids.length, 'task');
    const extra = res.ids.length - ids.length;
    toast(o.message ?? `Deleted ${first}${extra > 0 ? ` and ${plural(extra, 'sub-task')}` : ''}.`, {
      action: { label: 'Undo', run: () => { o.onUndo?.(); return restoreBatch(res.batch); } }, timeout: 8000,
    });
  } catch (e) { toastError(e); }
}

export async function restoreBatch(batch: string): Promise<void> {
  try { await api.restoreTasks(batch); await loadTasks(true); } catch (e) { toastError(e); }
}
export async function restoreTask(id: string): Promise<void> {
  try { await api.restoreTask(id); await loadTasks(true); } catch (e) { toastError(e); }
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
