// Tasks inside notes. A note shows a task with a {{task:id}} line; the task itself is a record
// (taskData.ts). This module draws such lines (the live preview widget, read mode and embeds
// through renderDefaults.taskHtml), opens the task in a popup when one is clicked, adds tasks to
// the open note from a popup (Cmd/Ctrl+Enter, or the note head's Task button) and paints the
// Tasks section of the note's side panel. The notes code reaches it only through taskHooks
// (notes/workspace.ts).
import type { EditorView } from '@codemirror/view';
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { emit, on, state } from '../../store.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml, modLabel, plural } from '../../util.js';
import { scan } from '../notes/mdscan.js';
import type { PortableTask } from '../notes/portable.js';
import type { TaskChoice } from '../notes/completion.js';
import { notesOpen, taskHooks, ws } from '../notes/workspace.js';
import { dueLabel, dueState, parseDue } from './dates.js';
import { childrenOf, createTask, loadTasks, plainTitle, progress, restoreTask, td, toggleTask } from './taskData.js';
import { type TaskForm, mountTaskForm } from './taskForm.js';
import { boxHtml, chipsHtml, ensureRenderer, installTaskRendering, refInnerHtml, refSig, titleHtml } from './taskRender.js';

const TOKEN = (id: string) => `{{task:${id}}}`;
const TOKEN_RE = /\{\{task:([a-z0-9]{4,32})\}\}/g;
// a line's lead (indentation, quote marks, list marker, a checkbox) and its text
const LINE = /^(\s*(?:>\s*)*)((?:[-*+]|\d+[.)])\s+)?(\[[ xX]\]\s+)?(.*)$/;
const HEADING = /^\s{0,3}#{1,6}\s/;
const TOKEN_LINE = /^\s*(?:>\s*)*(?:(?:[-*+]|\d+[.)])\s+)?\{\{task:[a-z0-9]{4,32}\}\}\s*$/;

export function initNoteTasks(): void {
  installTaskRendering();
  taskHooks.preview = {
    sig: refSig,
    fill(el, id) {
      el.dataset.task = id;
      el.classList.add('task-ref');
      el.classList.toggle('done', td.tasks.get(id)?.status === 'done');
      el.classList.toggle('missing', td.loaded && !td.tasks.has(id));
      el.innerHTML = refInnerHtml(id);
    },
    act: (id, act, el, view) => taskAct(id, act, el, view),
  };
  taskHooks.key = taskKey;
  taskHooks.add = (anchor) => { const v = ws.editor?.view; if (v) openNewTask(v, { anchor }); };
  taskHooks.paintSide = paintSide;
  taskHooks.click = onSideClick;
  taskHooks.saved = noteSaved;
  taskHooks.portable = portableTask;
  taskHooks.choices = taskChoices;
  taskHooks.titleHtml = (id) => { const t = td.tasks.get(id); return t ? `${boxHtml(t)} ${titleHtml(t.title)}` : ''; };
  taskHooks.create = async (title) => (await createTask({ title }))?.id ?? null;
  if (state.settings.notes_enabled !== false) loadTasks().catch(() => {});
  ensureRenderer().then(() => ws.editor?.refresh());
  on('tasks', () => {
    ws.editor?.refresh();
    // tasks drawn outside the editor (read mode, embeds): their content follows the task
    document.querySelectorAll<HTMLElement>('.task-ref[data-task]').forEach((el) => {
      if (el.closest('.cm-editor')) return;
      el.innerHTML = refInnerHtml(el.dataset.task!);
      el.classList.toggle('done', td.tasks.get(el.dataset.task!)?.status === 'done');
      el.classList.toggle('missing', td.loaded && !td.tasks.has(el.dataset.task!));
    });
    repaintSide();
  });
  // read mode and embeds: clicks on a drawn task
  document.addEventListener('click', (e) => {
    const t = e.target as Element;
    const ref = t.closest?.<HTMLElement>('.task-ref[data-task]');
    if (!ref || ref.closest('.cm-editor')) return;
    e.preventDefault();
    taskAct(ref.dataset.task!, t.closest<HTMLElement>('[data-task-act]')?.dataset.taskAct || 'open', ref, null);
  });
}

/** Which tasks each note shows, as last saved: when that changes, the notes of each task are loaded again. */
const shown = new Map<string, string>();
function noteSaved(path: string, text: string): void {
  const ids = [...new Set([...text.matchAll(TOKEN_RE)].map((m) => m[1]))].sort().join(',');
  const before = shown.get(path);
  shown.set(path, ids);
  const known = Object.entries(td.refs).filter(([, rs]) => rs.some((r) => r.path === path)).map(([id]) => id).sort().join(',');
  if (before !== undefined ? before !== ids : known !== ids) setTimeout(() => loadTasks(true).catch(() => {}), 300);   // after the index has the save
}

/** What "{{" offers in the editor: every task, with its date or state, and where it is shown. */
function taskChoices(): TaskChoice[] {
  const here = new Set([...(ws.session?.text ?? '').matchAll(TOKEN_RE)].map((m) => m[1]));
  return [...td.tasks.values()].map((t) => {
    const parent = t.parent_id ? td.tasks.get(t.parent_id) : undefined;
    const notes = (td.refs[t.id] || []).map((r) => r.title || r.path);
    return {
      id: t.id,
      title: plainTitle(t.title),
      detail: here.has(t.id) ? 'in this note' : t.status === 'done' ? 'done' : t.due ? dueLabel(t.due) : '',
      info: [parent ? `Sub-task of “${plainTitle(parent.title)}”` : '', notes.length ? `In ${notes.join(', ')}` : 'Not in a note yet'].filter(Boolean).join('\n'),
      done: t.status === 'done',
      updated: t.updated_at,
    };
  });
}

/** A task and its sub-tasks for the Markdown export (open sub-tasks and done ones alike). */
function portableTask(id: string): PortableTask | null {
  const t = td.tasks.get(id);
  if (!t) return null;
  return { title: t.title, done: t.status === 'done', subtasks: childrenOf(t.id).map((c) => portableTask(c.id)!).filter(Boolean) };
}

function taskAct(id: string, act: string, el: HTMLElement, view: EditorView | null): void {
  if (act === 'toggle') { toggleTask(id); return; }
  if (act === 'restore') { restoreTask(id); return; }
  if (act === 'remove') { removeRef(id, el, view); return; }
  openTaskPopup(id, el);
}

/** Take the {{task:id}} under ``el`` out of the open note (the task itself stays). */
function removeRef(id: string, el: HTMLElement, view: EditorView | null): void {
  const v = view || ws.editor?.view;
  if (!v) return;
  const doc = v.state.doc;
  let pos = -1;
  try { pos = view ? view.posAtDOM(el) : -1; } catch { /* not in the editor */ }
  const tok = TOKEN(id);
  const text = doc.toString();
  const at = pos >= 0 ? text.indexOf(tok, Math.max(0, pos - tok.length)) : text.indexOf(tok);
  if (at < 0) return;
  const line = doc.lineAt(at);
  const alone = LINE.exec(line.text)![4].trim() === tok;
  v.dispatch(alone
    ? { changes: { from: line.from, to: Math.min(doc.length, line.to + 1), insert: '' } }
    : { changes: { from: at, to: at + tok.length, insert: '' } });
}

/* ------------------------------------------------------------- popups */
let popup: { el: HTMLElement; close(): void } | null = null;

/** A panel near ``anchor`` (or a point) that closes on Esc and on a click elsewhere. */
function openPanel(cls: string, label: string, anchor: Element | { left: number; top: number; bottom: number }, onClose: () => void) {
  popup?.close();
  const el = document.createElement('div');
  el.className = `task-popup ${cls}`;
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-label', label);
  el.innerHTML = `<button type="button" class="icon-btn task-popup-close" title="Close (Esc)">${icon('close', 'icon-sm')}</button><div class="task-popup-body"></div>`;
  document.body.appendChild(el);
  const close = () => {
    if (!popup || popup.el !== el) return;
    popup = null;
    el.remove();
    document.removeEventListener('pointerdown', outside, true);
    document.removeEventListener('keydown', onKey, true);
    onClose();
  };
  const place = () => {
    const r = anchor instanceof Element ? anchor.getBoundingClientRect() : anchor;
    const w = el.offsetWidth, hgt = el.offsetHeight;
    const x = Math.max(12, Math.min(r.left, innerWidth - w - 12));
    let y = r.bottom + 6;
    if (y + hgt > innerHeight - 12) y = r.top - hgt - 6 >= 12 ? r.top - hgt - 6 : Math.max(12, innerHeight - hgt - 12);
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
  };
  const outside = (e: Event) => {
    const t = e.target as Element;
    if (el.contains(t) || t.closest?.('.menu, .dialog-backdrop, .cm-tooltip, .tf-calendar')) return;
    close();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Escape' || document.querySelector('.dialog-backdrop, .menu, .tf-calendar')) return;
    if ((e.target as Element).closest?.('.tf-title-input, .tf-date-input')) return;   // they cancel their own edit
    e.preventDefault(); e.stopPropagation(); close(); ws.editor?.focus();
  };
  el.querySelector('.task-popup-close')!.addEventListener('click', () => { close(); ws.editor?.focus(); });
  setTimeout(() => {
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', onKey, true);
  }, 0);
  popup = { el, close };
  return { el, body: el.querySelector<HTMLElement>('.task-popup-body')!, place, close };
}

/** The task, editable, in a panel next to where it was clicked. */
export function openTaskPopup(id: string, anchor: Element): void {
  let form: TaskForm | null = null;
  const p = openPanel('task-popup-edit', 'Task', anchor, () => form?.destroy());
  form = mountTaskForm(p.body, {
    popup: true,
    fromNote: notesOpen() ? ws.session?.path || null : null,
    onOpenTask: (next) => form!.show(next),
    onOpenInTasks: (tid) => emit('open-tasks', { task: tid }),
    onClose: p.close,
  });
  form.show(id);
  p.place();
  requestAnimationFrame(p.place);
}
export const closeTaskPopup = () => popup?.close();

/* ------------------------------------------------------------- adding a task to the note */
/** Where a new task's line goes: in place of a line (keeping its lead), or after it. */
interface Spot { line: number; text: string; lead: string; replace: boolean; title: string }

/** What the cursor's line offers: an empty line or one with text (its text becomes the title). */
function spotAt(view: EditorView, pos: number): Spot {
  const line = view.state.doc.lineAt(pos);
  const m = LINE.exec(line.text)!;
  const lead = m[1] + (m[2] || '');
  const rest = m[4].trim();
  if (HEADING.test(line.text) || /^(```|~~~|\$\$|\|)/.test(rest)) return { line: line.number, text: line.text, lead: '', replace: false, title: '' };
  return { line: line.number, text: line.text, lead, replace: true, title: rest };
}

/**
 * Put a task's line at ``spot``: in place of its line when that line was empty or its text became
 * the task (``useLine``), else after it. When the line changed meanwhile, after the cursor's line.
 */
function insertLine(view: EditorView, spot: Spot, id: string, useLine: boolean): void {
  const doc = view.state.doc;
  const tok = TOKEN(id);
  const same = spot.line <= doc.lines && doc.line(spot.line).text === spot.text;
  const line = same ? doc.line(spot.line) : doc.lineAt(view.state.selection.main.head);
  if (same && spot.replace && (useLine || !spot.title)) {
    const insert = spot.lead + tok;
    view.dispatch({ changes: { from: line.from, to: line.to, insert }, selection: { anchor: line.from + insert.length }, scrollIntoView: true });
  } else {
    const lead = same ? spot.lead : '';
    // after a paragraph, a blank line first: otherwise the task would join the paragraph
    const gap = !lead && line.text.trim() && !TOKEN_LINE.test(line.text) ? '\n' : '';
    const insert = `\n${gap}${lead}${tok}`;
    view.dispatch({ changes: { from: line.to, insert }, selection: { anchor: line.to + insert.length }, scrollIntoView: true });
  }
  view.focus();
}

/**
 * The popup for a new task in the open note: a title (the cursor line's text, if it has some),
 * a due date and details; or, as you type, one of the tasks you already have. Enter adds it.
 */
export function openNewTask(view: EditorView, { anchor = null as Element | null, spot: given = null as Spot | null } = {}): void {
  const head = view.state.selection.main.head;
  const spot = given || spotAt(view, head);
  const coords = view.coordsAtPos(view.state.doc.line(Math.min(spot.line, view.state.doc.lines)).from);
  const visible = coords && coords.top > 60 && coords.bottom < innerHeight - 40;
  const p = openPanel('task-popup-new', 'New task', anchor && !visible ? anchor : (coords || anchor || { left: 200, top: 120, bottom: 120 }), () => {});
  p.body.innerHTML = `
    <div class="tn-head">New task</div>
    <div class="tn-row tn-title"><span class="task-check ghost"></span>
      <input class="tn-title-input" placeholder="What needs doing? ($math$ and [@citations] work)" spellcheck="true" aria-label="Title" autocomplete="off"></div>
    <div class="tn-row tn-due">${icon('calendar')}<input class="tn-due-input" placeholder="Due date: fri, 27 oct, in 3 days…" spellcheck="false" aria-label="Due date" autocomplete="off"><span class="tn-due-hint muted"></span></div>
    <button type="button" class="link tn-more">${icon('text', 'icon-sm')} Add details</button>
    <textarea class="tn-body" rows="4" placeholder="Details (Markdown, like a note)" aria-label="Details" hidden></textarea>
    <div class="tn-existing" hidden></div>
    <div class="tn-foot"><span class="muted tn-where"></span><span class="spacer"></span>
      <button type="button" class="btn" data-tn="cancel">Cancel</button>
      <button type="button" class="btn btn-primary" data-tn="add">Add task</button></div>`;
  const $ = <T extends HTMLElement>(s: string) => p.body.querySelector<T>(s)!;
  const title = $<HTMLInputElement>('.tn-title-input');
  const due = $<HTMLInputElement>('.tn-due-input');
  const bodyEl = $<HTMLTextAreaElement>('.tn-body');
  const existing = $<HTMLElement>('.tn-existing');
  title.value = spot.title;
  $('.tn-where').textContent = spot.replace && spot.title ? 'Replaces this line' : 'Goes on a new line here';
  let matches: string[] = [];
  let at = -1;   // -1: the new task; else an existing one
  const inNote = new Set([...view.state.doc.toString().matchAll(TOKEN_RE)].map((m) => m[1]));

  const paintExisting = () => {
    const q = title.value.trim().toLowerCase();
    matches = q.length < 2 ? [] : [...td.tasks.values()]
      .filter((t) => !inNote.has(t.id) && plainTitle(t.title).toLowerCase().includes(q))
      .sort((a, b) => Number(a.status === 'done') - Number(b.status === 'done') || b.updated_at.localeCompare(a.updated_at))
      .slice(0, 5).map((t) => t.id);
    at = Math.min(at, matches.length - 1);
    existing.hidden = !matches.length;
    existing.innerHTML = matches.length ? `<div class="tn-sub muted">Or show a task you already have <span class="kbd">↓</span></div>${matches.map((id, i) => {
      const t = td.tasks.get(id)!;
      const ref = (td.refs[id] || [])[0];
      return `<button type="button" class="tn-match${i === at ? ' active' : ''}" data-i="${i}">${boxHtml(t)}<span class="nm">${titleHtml(t.title)}</span>${chipsHtml(t, { body: false })}
        <span class="muted where">${escapeHtml(ref ? ref.title || ref.path : 'not in a note')}</span></button>`;
    }).join('')}` : '';
    $('[data-tn="add"]').textContent = at >= 0 ? 'Show this task' : 'Add task';
  };
  const paintDue = () => {
    const v = due.value.trim();
    const d = v ? parseDue(v) : null;
    $('.tn-due').classList.toggle('invalid', !!v && !d);
    $('.tn-due-hint').textContent = !v ? '' : d ? `→ ${dueLabel(d)}${d !== v ? ` (${d})` : ''}` : 'not a date';
  };

  let busy = false;
  const submit = async () => {
    if (busy) return;
    if (at >= 0 && matches[at]) { p.close(); insertLine(view, spot, matches[at], false); return; }
    const t = title.value.replace(/\s+/g, ' ').trim();
    if (!t) { title.focus(); return; }
    const v = due.value.trim();
    const d = v ? parseDue(v) : null;
    if (v && !d) { due.focus(); due.select(); return; }
    busy = true;
    const made = await createTask({ title: t, due: d, body: bodyEl.value });
    busy = false;
    if (!made) return;
    p.close();
    insertLine(view, spot, made.id, spot.replace && !!spot.title && t === spot.title.replace(/\s+/g, ' ').trim());
  };

  title.addEventListener('input', () => { at = -1; paintExisting(); });
  due.addEventListener('input', paintDue);
  p.body.addEventListener('keydown', (e) => {
    const el = e.target as HTMLElement;
    if (e.key === 'Enter' && (el === title || el === due || ((e.metaKey || e.ctrlKey) && el === bodyEl))) { e.preventDefault(); submit(); return; }
    if (el === title && (e.key === 'ArrowDown' || e.key === 'ArrowUp') && matches.length) {
      e.preventDefault();
      at = Math.max(-1, Math.min(matches.length - 1, at + (e.key === 'ArrowDown' ? 1 : -1)));
      paintExisting();
    }
  });
  p.body.addEventListener('click', (e) => {
    const t = e.target as Element;
    const m = t.closest<HTMLElement>('.tn-match');
    if (m) { at = Number(m.dataset.i); submit(); return; }
    if (t.closest('.tn-more')) { bodyEl.hidden = false; t.closest('.tn-more')!.remove(); bodyEl.focus(); p.place(); return; }
    const b = t.closest<HTMLElement>('[data-tn]');
    if (b?.dataset.tn === 'add') submit();
    if (b?.dataset.tn === 'cancel') { p.close(); view.focus(); }
  });
  paintExisting();
  p.place();
  title.focus();
  title.select();
}

/**
 * Cmd/Ctrl+Enter in a note: on a line that shows a task, tick it; on a selection of checkbox lines,
 * turn them into tasks; anywhere else, the popup for a new task.
 */
export function taskKey(view: EditorView): boolean {
  const st = view.state;
  const sel = st.selection.main;
  const doc = st.doc;
  if (!sel.empty && doc.lineAt(sel.from).number !== doc.lineAt(sel.to).number
      && /^\s*(?:[-*+]|\d+[.)])\s+\[[ xX]\]\s+\S/m.test(st.sliceDoc(sel.from, sel.to))) {
    convertRange(view, doc.lineAt(sel.from).from, doc.lineAt(sel.to).to);
    return true;
  }
  const ids = [...doc.lineAt(sel.head).text.matchAll(TOKEN_RE)].map((m) => m[1]);
  if (ids.length) { toggleTask(ids[0]); return true; }
  openNewTask(view);
  return true;
}

/** Turn the checkbox items between two positions of the note into tasks (one undo step). */
async function convertRange(view: EditorView, from: number, to: number): Promise<void> {
  const text = view.state.sliceDoc(from, to);
  try {
    const res = await api.convertToTasks(text);
    if (view.state.sliceDoc(from, to) !== text) {
      toast('The note changed meanwhile: the tasks were made but not put in. They are under “Not in a note” in Tasks.');
      await loadTasks(true);
      return;
    }
    view.dispatch({ changes: { from, to, insert: res.text } });
    await loadTasks(true);
    toast(`Made ${plural(res.tasks.length, 'task')} from the checkboxes.`, { type: 'success' });
  } catch (e) { toastError(e); }
}

/* ------------------------------------------------------------- the side panel */
let sideBox: HTMLElement | null = null;
const repaintSide = () => { if (sideBox?.isConnected) paintSide(sideBox); };

/** The Tasks section of the open note's side panel: the tasks the note shows, from its text. */
function paintSide(box: HTMLElement): void {
  sideBox = box;
  const text = ws.session?.text ?? '';
  const ids: string[] = [];
  for (const l of scan(text).links) if (l.kind === 'task' && !ids.includes(l.target)) ids.push(l.target);
  box.hidden = false;
  if (!ids.length) {
    box.innerHTML = `<div class="ns-title">Tasks</div>
      <div class="side-empty">${modLabel}↩ or <button class="link" data-ns-task="add">${icon('plus', 'icon-sm')} Task</button> adds one here.</div>`;
    return;
  }
  const known = ids.map((id) => td.tasks.get(id)).filter((t) => !!t);
  const done = known.filter((t) => t!.status === 'done').length;
  const rows = ids.map((id) => {
    const t = td.tasks.get(id);
    if (!t) return `<div class="ns-task missing"><span class="task-check missing"></span><span class="nm muted">Deleted or unknown task</span></div>`;
    const p = progress(id);
    return `<div class="ns-task${t.status === 'done' ? ' done' : ''}" data-task="${id}">${boxHtml(t, 'data-ns-task="toggle"')}
      <span class="nm" data-ns-task="open">${titleHtml(t.title)}</span>
      ${p.total ? `<span class="task-chip progress">${p.done}/${p.total}</span>` : ''}
      ${t.due ? `<span class="task-chip due ${t.status === 'done' ? '' : dueState(t.due)}">${escapeHtml(dueLabel(t.due))}</span>` : ''}</div>`;
  }).join('');
  box.innerHTML = `<div class="ns-title">Tasks <span class="count">${done}/${known.length} done</span></div>
    <div class="ns-task-bar"><i style="width:${known.length ? (100 * done) / known.length : 0}%"></i></div>
    <div class="ns-task-list">${rows}</div>
    <div class="ns-task-actions">
      <button class="link" data-ns-task="add">${icon('plus', 'icon-sm')} Add a task</button>
      <button class="link" data-ns-task="show">${icon('tasks', 'icon-sm')} Show in Tasks</button></div>`;
}

/** Clicks in the side panel's Tasks section (notesView.ts passes clicks on through taskHooks). */
function onSideClick(e: MouseEvent): boolean {
  const t = e.target as Element;
  const a = t.closest<HTMLElement>('[data-ns-task]');
  if (!a || !a.closest('.ns-tasks')) return false;
  e.preventDefault();
  const id = a.closest<HTMLElement>('[data-task]')?.dataset.task;
  const act = a.dataset.nsTask;
  if (act === 'toggle' && id) toggleTask(id);
  else if (act === 'open' && id) openTaskPopup(id, a);
  else if (act === 'show') emit('open-tasks', { note: ws.session?.path || null });
  else if (act === 'add' && ws.editor) {
    // after the note's last task line (with its indentation and list marker), or at the end
    const v = ws.editor.view;
    const doc = v.state.doc;
    const last = [...doc.toString().matchAll(TOKEN_RE)].pop();
    const line = doc.lineAt(last ? last.index! : doc.length);
    const m = LINE.exec(line.text)!;
    const spot: Spot = last
      ? { line: line.number, text: line.text, lead: m[1] + (m[2] || ''), replace: false, title: '' }
      : { line: line.number, text: line.text, lead: '', replace: !line.text.trim(), title: '' };
    openNewTask(v, { anchor: a, spot });
  }
  return true;
}
