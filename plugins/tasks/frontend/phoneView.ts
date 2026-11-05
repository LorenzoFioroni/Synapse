// Tasks on a phone (its page in the phone layout's Plugins tab, or pinned to the tab bar): Today,
// Upcoming and All, big round boxes to tick, the note a task is in, and a quick-add field that
// reads a day at the end ("Reply to the referee fri"). A task opens in the same form the desktop's
// detail pane uses, as a screen of its own.
import type { PhoneNav } from '../../../frontend/src/plugins/api.js';
import type { Task } from '../../../frontend/src/plugins/task-runtime.js';
import { escapeHtml, icon, off, on, storage, store, toast, toastError } from '../../../frontend/src/plugins/task-runtime.js';
import { daysUntil, dueLabel, dueState, parseQuick, todayIso } from './dates.js';
import { SEGMENTS, type Segment, phoneGroups } from './phoneGroups.js';
import { allTasks, createTask, loadTasks, progress, td, toggleTask } from './taskData.js';
import { mountTaskForm } from './taskForm.js';
import { ensureRenderer, titleHtml } from './taskRender.js';

export function mountPhoneTasks(el: HTMLElement, nav: PhoneNav): () => void {
  let seg = storage<Segment>('zc.phone.tasks', 'today');
  el.classList.add('tp-phone');
  el.innerHTML = `<div class="tp-segments" role="tablist"></div><div class="tp-list"></div>
    <form class="tp-add"><span class="tp-add-icon">${icon('plus')}</span><input placeholder="New task, e.g. “reply to the referee fri”" aria-label="New task" autocomplete="off"><button type="submit" class="tp-add-go" aria-label="Add">${icon('check')}</button></form>`;
  const list = el.querySelector<HTMLElement>('.tp-list')!;
  const paint = () => {
    const tasks = allTasks();
    const counts: Record<Segment, number> = { today: 0, upcoming: 0, all: 0 };
    for (const [s] of SEGMENTS) counts[s] = phoneGroups(tasks, td.refs, s).reduce((n, g) => n + g.tasks.length, 0);
    el.querySelector('.tp-segments')!.innerHTML = SEGMENTS.map(([s, name]) =>
      `<button role="tab" data-seg="${s}" aria-selected="${s === seg}">${name}${counts[s] ? ` · ${counts[s]}` : ''}</button>`).join('');
    if (!td.loaded) { list.innerHTML = '<div class="tp-empty">Loading…</div>'; return; }
    const groups = phoneGroups(tasks, td.refs, seg);
    list.innerHTML = groups.length ? groups.map((g) => `<h2 class="tp-group ${g.tone}">${escapeHtml(g.label)}</h2>
      <div class="tp-box">${g.tasks.map(row).join('')}</div>`).join('')
      : `<div class="tp-empty">${icon('check')}<h3>${seg === 'today' ? 'Nothing due today' : 'Nothing here'}</h3><p>Add a task below, or from a note.</p></div>`;
  };
  const row = (t: Task) => {
    const ref = (td.refs[t.id] || [])[0];
    const p = progress(t.id);
    const st = t.due ? dueState(t.due) : '';
    return `<div class="tp-row" data-task-row="${escapeHtml(t.id)}">
      <button class="tp-check" data-tp="toggle" aria-label="Mark “${escapeHtml(t.title)}” as done"></button>
      <button class="tp-main" data-tp="open"><span class="tp-title">${titleHtml(t.title)}</span>
        <span class="tp-meta">${t.due && seg !== 'today' ? `<span class="tp-due ${st}">${escapeHtml(dueLabel(t.due))}</span>` : ''}${ref ? `<span class="tp-note" data-tp="note" data-path="${escapeHtml(ref.path)}">${escapeHtml(ref.title || ref.path)}</span>` : ''}${p.total ? `<span>${p.done} of ${p.total} sub-tasks</span>` : ''}${t.body.trim() ? `<span>${icon('text')}</span>` : ''}</span></button></div>`;
  };
  el.addEventListener('click', (e) => {
    const t = e.target as Element;
    const s = t.closest<HTMLElement>('[data-seg]');
    if (s) { seg = s.dataset.seg as Segment; store('zc.phone.tasks', seg); paint(); return; }
    const r = t.closest<HTMLElement>('[data-task-row]');
    if (!r) return;
    const id = r.dataset.taskRow!;
    const act = t.closest<HTMLElement>('[data-tp]')?.dataset.tp;
    if (act === 'toggle') {
      r.classList.add('done');
      void toggleTask(id);
      toast('Done', { action: { label: 'Undo', run: () => toggleTask(id) }, timeout: 3500 });
      return;
    }
    if (act === 'note') { nav.openNote(t.closest<HTMLElement>('[data-path]')!.dataset.path!); return; }
    if (act === 'open') openTask(id, nav);
  });
  el.querySelector('form')!.addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = el.querySelector<HTMLInputElement>('.tp-add input')!;
    const q = parseQuick(input.value);
    if (!q.title) return;
    input.value = '';
    try {
      const t = await createTask({ title: q.title, due: q.due ?? (seg === 'today' ? todayIso() : null) });
      if (t && q.due) toast(`Added, due ${dueLabel(q.due)}`, { type: 'success', timeout: 2500 });
    } catch (err) { toastError(err); }
  });
  on('tasks', paint);
  paint();
  void loadTasks();
  void ensureRenderer().then(paint);
  return () => off('tasks', paint);
}

/** A task's own screen: the form the desktop shows beside the list. */
function openTask(id: string, nav: PhoneNav): void {
  nav.push({
    title: 'Task',
    mount: (box) => {
      box.classList.add('tp-detail');
      const form = mountTaskForm(box, { onOpenTask: (other) => openTask(other, nav), onClose: () => nav.back() });
      form.show(id);
      return () => form.destroy();
    },
  });
}

/** How many open tasks are due today or overdue (the badge on the Tasks tab). */
export function dueCount(now: Date = new Date()): number {
  return allTasks().filter((t) => t.status !== 'done' && t.due && daysUntil(t.due, now) <= 0).length;
}

/** "Task" in the "+" sheet: one line, a day at the end sets the due date. */
export async function quickTask(prompt: (label: string, o: { title?: string; placeholder?: string; confirm?: string; hint?: string }) => Promise<string | null>): Promise<void> {
  const text = await prompt('Task', { title: 'New task', placeholder: 'Reply to the referee fri', confirm: 'Add', hint: 'A day at the end sets when it is due.' });
  if (!text?.trim()) return;
  const q = parseQuick(text);
  try {
    await createTask({ title: q.title, due: q.due });
    toast(q.due ? `Task added, due ${dueLabel(q.due)}` : 'Task added', { type: 'success', timeout: 3000 });
  } catch (e) { toastError(e); }
}
