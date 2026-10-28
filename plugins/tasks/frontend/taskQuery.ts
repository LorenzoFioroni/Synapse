// What the Tasks page shows: the tasks of a view, filtered by the search, as a tree whose siblings
// are sorted, cut into groups. No DOM (tested in tests/js/tasks.test.mjs). The search syntax is the
// same as backend/tasks/query.py (list_tasks for Claude).
import type { Task, TaskRef } from '../../../frontend/src/plugins/task-runtime.js';
import { daysUntil } from './dates.js';

export type ViewId = 'open' | 'today' | 'upcoming' | 'nodate' | 'unfiled' | 'done' | 'all';
export type SortId = 'position' | 'due' | 'title' | 'created' | 'updated' | 'done';
export type GroupId = 'note' | 'due' | 'none';

export const VIEWS: Record<ViewId, { name: string; icon: string; closed?: boolean }> = {
  open: { name: 'Open', icon: 'tasks' },
  today: { name: 'Today', icon: 'today' },
  upcoming: { name: 'Next 7 days', icon: 'upcoming' },
  nodate: { name: 'No date', icon: 'nodate' },
  unfiled: { name: 'Not in a note', icon: 'inbox' },
  done: { name: 'Done', icon: 'check', closed: true },
  all: { name: 'All tasks', icon: 'list' },
};
export const SORTS: Record<SortId, string> = {
  position: 'Order', due: 'Due date', title: 'Title', created: 'Date created', updated: 'Last changed', done: 'Date done',
};
export const GROUPS: Record<GroupId, string> = { note: 'Note', due: 'Due date', none: 'None' };

type Refs = Record<string, TaskRef[]>;
type Test = (t: Task) => boolean;

/** Words and filters of a search (see docs/tasks.md, "Search syntax"). */
export function parseQuery(q: string, refs: Refs, kids: Map<string, Task[]>, now: Date = new Date()): Test {
  const tokens = q.match(/-?"[^"]*"?|\S+/g) || [];
  const tests: Test[] = [];
  for (const raw of tokens) {
    const neg = raw.startsWith('-') && raw.length > 1;
    const tok = (neg ? raw.slice(1) : raw).toLowerCase();
    let test: Test;
    if (tok.startsWith('is:')) { const v = tok.slice(3); test = (t) => t.status === v; }
    else if (tok.startsWith('due:')) test = dueTest(tok.slice(4), now);
    else if (tok === 'has:body') test = (t) => !!t.body.trim();
    else if (tok === 'has:subtasks') test = (t) => (kids.get(t.id) || []).length > 0;
    else if (tok.startsWith('note:')) {
      const v = tok.slice(5).replace(/^"|"$/g, '');
      test = (t) => (refs[t.id] || []).some((r) => (r.title || '').toLowerCase().includes(v) || r.path.toLowerCase().includes(v));
    } else {
      const w = tok.replace(/^"|"$/g, '');
      test = (t) => `${t.title}\n${t.body}`.toLowerCase().includes(w);
    }
    tests.push(neg ? (t) => !test(t) : test);
  }
  return (t) => tests.every((f) => f(t));
}

function dueTest(v: string, now: Date): Test {
  if (v === 'none') return (t) => !t.due;
  if (v === 'today') return (t) => !!t.due && daysUntil(t.due, now) === 0;
  if (v === 'overdue') return (t) => !!t.due && t.status !== 'done' && daysUntil(t.due, now) < 0;
  if (v === 'week') return (t) => !!t.due && daysUntil(t.due, now) <= 7;
  if (v.startsWith('<')) return (t) => !!t.due && t.due < v.slice(1);
  if (v.startsWith('>')) return (t) => !!t.due && t.due > v.slice(1);
  return (t) => t.due === v;
}

/** Does a note show the task, or one of its parents? (A sub-task is in its parent's note.) */
export function inSomeNote(t: Task, refs: Refs, byId?: Map<string, Task>): boolean {
  for (let x: Task | undefined = t; x; x = x.parent_id ? byId?.get(x.parent_id) : undefined) if ((refs[x.id] || []).length) return true;
  return false;
}

/** Is the task in the view (before the search)? ``byId`` finds its parents (for "Not in a note"). */
export function inView(view: ViewId, t: Task, refs: Refs, now: Date = new Date(), byId?: Map<string, Task>): boolean {
  const open = t.status !== 'done';
  switch (view) {
    case 'open': return open;
    case 'today': return open && !!t.due && daysUntil(t.due, now) <= 0;
    case 'upcoming': return open && !!t.due && daysUntil(t.due, now) >= 0 && daysUntil(t.due, now) <= 7;
    case 'nodate': return open && !t.due;
    case 'unfiled': return open && !inSomeNote(t, refs, byId);
    case 'done': return !open;
    default: return true;
  }
}

export interface QueryOptions {
  view: ViewId;
  query?: string;
  sort?: SortId;
  group?: GroupId;
  showDone?: boolean;      // sub-tasks that are done, in views of open tasks
  flat?: boolean;          // no hierarchy: every match on its own row
  folded?: Set<string>;
  note?: string | null;    // only tasks shown in this note (and their sub-tasks)
  keep?: Set<string>;      // shown whatever the view says (just ticked on the page: Space ticks them back)
  now?: Date;
}

export interface TaskRow {
  type: 'task';
  task: Task;
  depth: number;
  context: boolean;        // shown only because a sub-task matches
  kids: number;            // sub-tasks (all of them, whatever is shown)
  kidsDone: number;
  folded: boolean;
  shownKids: number;
}
export interface GroupRow { type: 'group'; key: string; label: string; note?: string | null; state?: string; count: number }
export type Row = TaskRow | GroupRow;

/** Everything below each task, by parent id (a task's children in their stored order). */
export function childrenOf(tasks: Task[]): Map<string, Task[]> {
  const out = new Map<string, Task[]>();
  for (const t of tasks) if (t.parent_id) (out.get(t.parent_id) || out.set(t.parent_id, []).get(t.parent_id)!).push(t);
  for (const list of out.values()) list.sort((a, b) => a.position - b.position);
  return out;
}

/** The task's own date, else the earliest date among its open sub-tasks (any depth). */
export function effectiveDue(t: Task, kids: Map<string, Task[]>): string | null {
  let best = t.due;
  for (const c of kids.get(t.id) || []) {
    if (c.status === 'done') continue;
    const d = effectiveDue(c, kids);
    if (d && (!best || d < best)) best = d;
  }
  return best;
}

export function buildRows(tasks: Task[], refs: Refs, o: QueryOptions): { rows: Row[]; matches: number } {
  const now = o.now || new Date();
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const kids = childrenOf(tasks);
  const search = parseQuery(o.query || '', refs, kids, now);
  const viewDone = !!VIEWS[o.view]?.closed || o.view === 'all';
  const statusOK = (t: Task) => o.showDone || viewDone || t.status !== 'done' || !!o.keep?.has(t.id);
  const inNote = (t: Task): boolean => {
    if (!o.note) return true;
    for (let x: Task | undefined = t; x; x = x.parent_id ? byId.get(x.parent_id) : undefined) {
      if ((refs[x.id] || []).some((r) => r.path === o.note)) return true;
    }
    return false;
  };
  const match = (t: Task) => !!o.keep?.has(t.id) || (inView(o.view, t, refs, now, byId) && inNote(t) && search(t));

  const shown = new Map<string, 'match' | 'ctx' | 'child'>();
  for (const t of tasks) if (match(t)) shown.set(t.id, 'match');
  const matches = shown.size;
  if (!o.flat) {
    for (const id of [...shown.keys()]) {
      for (let p = byId.get(id)?.parent_id; p && byId.has(p); p = byId.get(p)!.parent_id) if (!shown.has(p)) shown.set(p, 'ctx');
      const addKids = (pid: string) => {
        for (const c of kids.get(pid) || []) if (statusOK(c)) { if (!shown.has(c.id)) shown.set(c.id, 'child'); addKids(c.id); }
      };
      addKids(id);
    }
  }
  const visible = tasks.filter((t) => shown.has(t.id));
  const parentOf = (t: Task) => (!o.flat && t.parent_id && shown.has(t.parent_id) ? t.parent_id : null);
  const firstRef = (t: Task): TaskRef | null => {
    for (let x: Task | undefined = t; x; x = x.parent_id ? byId.get(x.parent_id) : undefined) {
      const r = refs[x.id]?.find((y) => !o.note || y.path === o.note) || refs[x.id]?.[0];
      if (r) return r;
    }
    return null;
  };

  const sort = o.sort || 'position';
  const key = (t: Task): (string | number)[] => {
    const ref = firstRef(t);
    const where: (string | number)[] = [ref ? ref.path : '\uffff', ref ? ref.line : 0, t.position];
    switch (sort) {
      case 'due': return [effectiveDue(t, kids) || '9999-99-99', ...where];
      case 'title': return [t.title.toLowerCase(), ...where];
      case 'created': return [`~${invert(t.created_at)}`, ...where];
      case 'updated': return [`~${invert(t.updated_at)}`, ...where];
      case 'done': return [`~${invert(t.done_at || '')}`, ...where];
      default: return where;
    }
  };
  const cmp = (a: Task, b: Task) => compareKeys(key(a), key(b));
  const roots = visible.filter((t) => !parentOf(t)).sort(cmp);
  const children = new Map<string, Task[]>();
  for (const t of visible) { const p = parentOf(t); if (p) (children.get(p) || children.set(p, []).get(p)!).push(t); }
  for (const list of children.values()) list.sort(cmp);

  const group = o.group || 'none';
  const groupOf = (t: Task): Omit<GroupRow, 'type' | 'count'> => {
    if (group === 'note') {
      const r = firstRef(t);
      return r ? { key: `n:${r.path}`, label: r.title || r.path.replace(/\.md$/, ''), note: r.path } : { key: 'n:\uffff', label: 'Not in a note', note: null };
    }
    if (group === 'due') {
      const d = effectiveDue(t, kids);
      if (!d) return { key: '9', label: 'No date' };
      const n = daysUntil(d, now);
      return n < 0 ? { key: '0', label: 'Overdue', state: 'overdue' } : n === 0 ? { key: '1', label: 'Today', state: 'today' }
        : n === 1 ? { key: '2', label: 'Tomorrow' } : n <= 7 ? { key: '3', label: 'Next 7 days' } : { key: '4', label: 'Later' };
    }
    return { key: '', label: '' };
  };

  const groups = new Map<string, GroupRow & { items: Task[] }>();
  for (const r of roots) {
    const g = groupOf(r);
    if (!groups.has(g.key)) groups.set(g.key, { type: 'group', ...g, count: 0, items: [] });
    groups.get(g.key)!.items.push(r);
  }
  const rows: Row[] = [];
  const counted = (t: Task): number => (shown.get(t.id) !== 'ctx' && t.status !== 'done' ? 1 : 0)
    + (children.get(t.id) || []).reduce((s, c) => s + counted(c), 0);
  const walk = (t: Task, depth: number) => {
    const all = kids.get(t.id) || [];
    const mine = children.get(t.id) || [];
    const folded = !!o.folded?.has(t.id);
    rows.push({ type: 'task', task: t, depth, context: shown.get(t.id) === 'ctx', kids: all.length,
      kidsDone: all.filter((c) => c.status === 'done').length, folded, shownKids: mine.length });
    if (!folded) for (const c of mine) walk(c, depth + 1);
  };
  const ordered = [...groups.values()].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  for (const g of ordered) {
    if (group !== 'none') {
      const { items, ...head } = g;
      rows.push({ ...head, count: items.reduce((s, t) => s + counted(t), 0) });
    }
    for (const t of g.items) walk(t, 0);
  }
  return { rows, matches };
}

/** Newest first, as a string that sorts ascending. */
const invert = (iso: string) => [...iso].map((c) => (/\d/.test(c) ? String(9 - Number(c)) : c)).join('');

function compareKeys(a: (string | number)[], b: (string | number)[]): number {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i], y = b[i];
    if (x === y) continue;
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    if (typeof x === 'number' && typeof y === 'number') return x - y;
    return String(x) < String(y) ? -1 : 1;
  }
  return 0;
}

/** Counts for the views column: open tasks of each view, and those overdue. */
export function viewCounts(tasks: Task[], refs: Refs, now: Date = new Date()): Record<ViewId | 'overdue', number> {
  const out = { open: 0, today: 0, upcoming: 0, nodate: 0, unfiled: 0, done: 0, all: tasks.length, overdue: 0 } as Record<ViewId | 'overdue', number>;
  const byId = new Map(tasks.map((t) => [t.id, t]));
  for (const t of tasks) {
    for (const v of ['open', 'today', 'upcoming', 'nodate', 'unfiled', 'done'] as ViewId[]) if (inView(v, t, refs, now, byId)) out[v]++;
    if (t.status !== 'done' && t.due && daysUntil(t.due, now) < 0) out.overdue++;
  }
  return out;
}
