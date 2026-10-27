// How a task looks wherever it is drawn: in the Tasks page, in a note (live preview and read
// mode, through renderDefaults.taskHtml) and in exports. Titles and bodies are Markdown rendered
// like notes (math, citations, [[links]]).
import { icon } from '../../../frontend/src/plugins/task-runtime.js';
import type { Task } from '../../../frontend/src/plugins/task-runtime.js';
import { escapeHtml, plural } from '../../../frontend/src/plugins/task-runtime.js';
import { loadMD } from '../../../frontend/src/plugins/task-runtime.js';
import { type Renderer, createRenderer, renderDefaults } from './rendering.js';
import { citeInfo, equationMode, noteMacros, normTarget, ws } from './environment.js';
import { dueLabel, dueState } from './dates.js';
import { progress, td } from './taskData.js';
import type { TaskBlockOptions } from './rendering.js';

let renderer: Renderer | null = null;
let loading: Promise<Renderer> | null = null;

/** [[target]] in a task: resolved against all notes (a task belongs to no single note). */
export function resolveAnywhere(target: string): string | null | undefined {
  const t = normTarget(target || '');
  if (!t || !ws.overview.notes.length) return undefined;
  const hit = ws.overview.notes.filter((n) => n.path.toLowerCase().replace(/\.md$/, '') === t
    || n.path.toLowerCase().replace(/\.md$/, '').endsWith(`/${t}`) || n.path.toLowerCase().split('/').pop()!.replace(/\.md$/, '') === t);
  return hit.length ? hit.sort((a, b) => a.path.length - b.path.length)[0].path : null;
}

export function ensureRenderer(): Promise<Renderer> {
  if (renderer) return Promise.resolve(renderer);
  loading ||= loadMD().then((lib) => {
    renderer = createRenderer(lib, { citeInfo, resolveNote: resolveAnywhere, equations: equationMode, macros: noteMacros });
    return renderer;
  });
  return loading;
}
export const taskRenderer = (): Renderer | null => renderer;

/** A title as HTML (escaped text until the renderer has loaded). */
export function titleHtml(md: string): string {
  if (!md.trim()) return '<span class="muted">Untitled</span>';
  return renderer ? renderer.renderInline(md) : escapeHtml(md);
}
export function bodyHtml(md: string): string {
  return renderer ? renderer.render(md) : `<p>${escapeHtml(md)}</p>`;
}

export function boxHtml(t: Pick<Task, 'status'>, attrs = ''): string {
  return `<span class="task-check${t.status === 'done' ? ' done' : ''}" role="checkbox" aria-checked="${t.status === 'done'}" ${attrs}>${t.status === 'done' ? icon('check') : ''}</span>`;
}

/** The small labels after a title: due date, sub-task progress, details. */
export function chipsHtml(t: Task, { sub = true, body = true } = {}): string {
  const out: string[] = [];
  if (t.due) {
    const st = t.status === 'done' ? '' : dueState(t.due);
    out.push(`<span class="task-chip due ${st}" title="Due ${escapeHtml(t.due)}">${icon('calendar')}${escapeHtml(dueLabel(t.due))}</span>`);
  }
  if (sub) {
    const p = progress(t.id);
    if (p.total) out.push(`<span class="task-chip progress" title="${p.done} of ${plural(p.total, 'sub-task')} done">${ring(p.done, p.total)}${p.done}/${p.total}</span>`);
  }
  if (body && t.body.trim()) out.push(`<span class="task-chip details" title="Has details">${icon('text')}</span>`);
  return out.length ? `<span class="task-chips">${out.join('')}</span>` : '';
}

function ring(done: number, total: number): string {
  const r = 4.2, c = 2 * Math.PI * r, f = total ? done / total : 0;
  return `<svg class="task-ring" viewBox="0 0 11 11" aria-hidden="true"><circle cx="5.5" cy="5.5" r="${r}" fill="none" class="track" stroke-width="1.6"/>`
    + `<circle cx="5.5" cy="5.5" r="${r}" fill="none" class="fill" stroke-width="1.6" stroke-dasharray="${(c * f).toFixed(2)} ${c.toFixed(2)}" transform="rotate(-90 5.5 5.5)"/></svg>`;
}

/** What a {{task:id}} line in a note shows (its inner HTML). */
export function refInnerHtml(id: string): string {
  const t = td.tasks.get(id);
  if (!t) {
    if (!td.loaded) return `<span class="task-check"></span><span class="task-text"><span class="task-title muted">Loading task…</span></span>`;
    return `<span class="task-check missing"></span><span class="task-text"><span class="task-title muted">Deleted or unknown task</span>`
      + `<span class="task-ref-actions"><button type="button" class="link" data-task-act="restore">Restore</button>`
      + `<button type="button" class="link" data-task-act="remove">Remove</button></span></span>`;
  }
  // the title and its labels flow as one text, so a long title wraps and the labels follow it
  return `${boxHtml(t, 'data-task-act="toggle"')}<span class="task-text"><span class="task-title" data-task-act="open">${titleHtml(t.title)}</span>${chipsHtml(t)}</span>`;
}

/** The whole {{task:id}} element, for read mode and exports (renderDefaults.taskHtml). */
export function refHtml(id: string): string {
  const t = td.tasks.get(id);
  return `<span class="task-ref${t?.status === 'done' ? ' done' : ''}${t ? '' : ' missing'}" data-task="${escapeHtml(id)}">${refInnerHtml(id)}</span>`;
}

export type TaskBlockUi = { filter?: 'all' | 'open' | 'done'; sort?: 'manual' | 'deadline' | 'title' };

function taskBlockRows(ids: string[], options: TaskBlockOptions, ui: TaskBlockUi = {}): { label: string; ids: string[] }[] {
  let rows = ids.filter((id) => options.completed !== 'hide' || td.tasks.get(id)?.status !== 'done');
  if (ui.filter === 'open') rows = rows.filter((id) => td.tasks.get(id)?.status !== 'done');
  if (ui.filter === 'done') rows = rows.filter((id) => td.tasks.get(id)?.status === 'done');
  if (options.completed === 'bottom') rows = [...rows.filter((id) => td.tasks.get(id)?.status !== 'done'), ...rows.filter((id) => td.tasks.get(id)?.status === 'done')];
  if (ui.sort === 'deadline') rows = [...rows].sort((a, b) => (td.tasks.get(a)?.due || '9999').localeCompare(td.tasks.get(b)?.due || '9999'));
  if (ui.sort === 'title') rows = [...rows].sort((a, b) => (td.tasks.get(a)?.title || '').localeCompare(td.tasks.get(b)?.title || ''));
  if (options.group !== 'deadline') return [{ label: '', ids: rows }];
  const groups = new Map<string, string[]>();
  for (const id of rows) {
    const t = td.tasks.get(id);
    const key = t?.status === 'done' && options.completed === 'bottom' ? 'Completed' : t?.due || 'No deadline';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(id);
  }
  return [...groups].sort(([a], [b]) => {
    if (a === 'Completed') return 1; if (b === 'Completed') return -1;
    if (a === 'No deadline') return 1; if (b === 'No deadline') return -1;
    return a.localeCompare(b);
  }).map(([key, taskIds]) => ({ label: /^\d{4}-\d{2}-\d{2}$/.test(key) ? dueLabel(key) : key, ids: taskIds }));
}

/** A fenced task block in read/live modes and HTML/PDF exports. */
export function taskBlockHtml(ids: string[], options: TaskBlockOptions, ui: TaskBlockUi = {}): string {
  const filter = ui.filter || 'all', sort = ui.sort || 'manual';
  const controls = `<div class="task-block-controls" data-task-block-controls>
    <span class="segmented" role="group" aria-label="Filter tasks">${(['all', 'open', 'done'] as const).map((v) => `<button type="button" data-task-block-filter="${v}" class="${filter === v ? 'active' : ''}">${v[0].toUpperCase() + v.slice(1)}</button>`).join('')}</span>
    <button type="button" class="btn btn-sm btn-ghost" data-task-block-act="sort" title="Change task order">${icon('sort', 'icon-sm')} ${sort === 'manual' ? 'Manual' : sort === 'deadline' ? 'Deadline' : 'Title'}</button>
    <span class="spacer"></span><button type="button" class="btn btn-sm" data-task-block-act="add">${icon('plus', 'icon-sm')} Add task</button></div>`;
  const groups = taskBlockRows(ids, options, ui);
  if (!groups.some((g) => g.ids.length)) return `<div class="task-block ${options.view}" data-task-block>${controls}<div class="task-block-empty">No tasks match this filter.</div></div>`;
  const content = groups.map((group) => {
    const heading = group.label ? `<div class="task-block-group">${escapeHtml(group.label)}</div>` : '';
    if (options.view === 'table') {
      const rows = group.ids.map((id) => {
        const t = td.tasks.get(id);
        const status = t ? (t.status === 'done' ? 'Done' : 'Open') : 'Missing';
        return `<tr><td>${refHtml(id)}</td><td>${escapeHtml(t?.due ? dueLabel(t.due) : '—')}</td><td>${status}</td></tr>`;
      }).join('');
      return `${heading}<table class="task-block-table"><thead><tr><th>Task</th><th>Deadline</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
    return `${heading}<div class="task-block-list">${group.ids.map(refHtml).join('')}</div>`;
  }).join('');
  return `<div class="task-block ${options.view}" data-task-block>${controls}${content}</div>`;
}

/** Install the task drawing into every Markdown renderer. */
export function installTaskRendering(): void {
  renderDefaults.taskHtml = refHtml;
  renderDefaults.taskBlockHtml = taskBlockHtml;
  ensureRenderer();
}

/** A signature of what a task's line shows, for widget keys (changes when it must be drawn again). */
export function refSig(id: string): string {
  const t = td.tasks.get(id);
  if (!t) return `${id}:${td.loaded ? 'missing' : 'loading'}`;
  const p = progress(id);
  return `${id}:${t.status}:${t.title}:${t.due}:${p.done}/${p.total}:${t.body.trim() ? 1 : 0}:${dueState(t.due)}:${renderer ? 1 : 0}`;
}
