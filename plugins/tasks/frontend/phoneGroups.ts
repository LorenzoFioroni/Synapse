// What the Tasks page shows on a phone (phoneView.ts), without a DOM (tests/js/tasks.test.mjs):
// open tasks due today (with those overdue), in the next week, or all, in groups by day.
import type { Task, TaskRef } from '../../../frontend/src/plugins/task-runtime.js';
import { daysUntil, dueLabel } from './dates.js';
import { inView } from './taskQuery.js';

export type Segment = 'today' | 'upcoming' | 'all';
export const SEGMENTS: [Segment, string][] = [['today', 'Today'], ['upcoming', 'Upcoming'], ['all', 'All']];

/** What a phone shows of the open tasks: today's (with those overdue), the next week's, all. */
export function phoneGroups(tasks: Task[], refs: Record<string, TaskRef[]>, seg: Segment, now: Date = new Date()): { label: string; tone: string; tasks: Task[] }[] {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const shown = tasks.filter((t) => {
    if (t.status === 'done') return false;
    const parent = t.parent_id ? byId.get(t.parent_id) : undefined;
    if (parent && parent.status !== 'done' && inSeg(parent, seg)) return false;   // shown under its parent
    return inSeg(t, seg);
  });
  function inSeg(t: Task, s: Segment): boolean {
    if (s === 'today') return inView('today', t, refs, now, byId);
    if (s === 'upcoming') return !!t.due && daysUntil(t.due, now) > 0 && daysUntil(t.due, now) <= 7;
    return t.status !== 'done';
  }
  const order = (a: Task, b: Task) => (a.due || '9999').localeCompare(b.due || '9999') || a.position - b.position;
  const groups = new Map<string, { label: string; tone: string; tasks: Task[] }>();
  for (const t of shown.sort(order)) {
    const n = t.due ? daysUntil(t.due, now) : null;
    const [key, label, tone] = n === null ? ['none', 'No date', '']
      : n < 0 ? ['overdue', 'Overdue', 'overdue'] : n === 0 ? ['today', 'Today', '']
      : seg === 'upcoming' || n <= 7 ? [t.due!, dueLabel(t.due!, now), ''] : ['later', 'Later', ''];
    if (!groups.has(key)) groups.set(key, { label, tone, tasks: [] });
    groups.get(key)!.tasks.push(t);
  }
  return [...groups.values()];
}
