// How a task looks wherever it is drawn: in the Tasks page, in a note (live preview and read
// mode, through renderDefaults.taskHtml) and in exports. Titles and bodies are Markdown rendered
// like notes (math, citations, [[links]]).
import { icon } from '../../icons.js';
import type { Task } from '../../types.js';
import { escapeHtml, plural } from '../../util.js';
import { loadMD } from '../notes/editor.js';
import { type Renderer, createRenderer, renderDefaults } from '../notes/render.js';
import { citeInfo, equationMode, noteMacros, normTarget, ws } from '../notes/workspace.js';
import { dueLabel, dueState } from './dates.js';
import { progress, td } from './taskData.js';

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
    if (!td.loaded) return `<span class="task-check"></span><span class="task-title muted">Loading task…</span>`;
    return `<span class="task-check missing"></span><span class="task-title muted">Deleted or unknown task</span>`
      + `<span class="task-ref-actions"><button type="button" class="link" data-task-act="restore">Restore</button>`
      + `<button type="button" class="link" data-task-act="remove">Remove</button></span>`;
  }
  return `${boxHtml(t, 'data-task-act="toggle"')}<span class="task-title" data-task-act="open">${titleHtml(t.title)}</span>${chipsHtml(t)}`;
}

/** The whole {{task:id}} element, for read mode and exports (renderDefaults.taskHtml). */
export function refHtml(id: string): string {
  const t = td.tasks.get(id);
  return `<span class="task-ref${t?.status === 'done' ? ' done' : ''}${t ? '' : ' missing'}" data-task="${escapeHtml(id)}">${refInnerHtml(id)}</span>`;
}

/** Install the task drawing into every Markdown renderer. */
export function installTaskRendering(): void {
  renderDefaults.taskHtml = refHtml;
  ensureRenderer();
}

/** A signature of what a task's line shows, for widget keys (changes when it must be drawn again). */
export function refSig(id: string): string {
  const t = td.tasks.get(id);
  if (!t) return `${id}:${td.loaded ? 'missing' : 'loading'}`;
  const p = progress(id);
  return `${id}:${t.status}:${t.title}:${t.due}:${p.done}/${p.total}:${t.body.trim() ? 1 : 0}:${dueState(t.due)}:${renderer ? 1 : 0}`;
}
