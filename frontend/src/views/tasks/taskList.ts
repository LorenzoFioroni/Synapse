// The list of the Tasks page: rows as a tree (taskQuery.ts decides which), selection, the
// keyboard, the row menu, renaming in place, drag and drop, and Undo of the page's own changes.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import type { Task, TaskChanges } from '../../types.js';
import { openMenu } from '../../ui/menu.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml, modKey, modLabel, plural } from '../../util.js';
import { parseDue, parseQuick } from './dates.js';
import { openNoteAt, pickNote } from './notePicker.js';
import { type Row, type TaskRow, buildRows } from './taskQuery.js';
import { createTask, deleteTasks, loadTasks, patchTask, restoreBatch, td, toggleTask } from './taskData.js';
import { boxHtml, chipsHtml, titleHtml } from './taskRender.js';
import { type ListState } from './tasksState.js';

export interface ListHost {
  el: HTMLElement;
  state: ListState;
  /** The selection changed (the detail pane follows). */
  onSelect(): void;
  /** Ask the page to repaint everything that depends on the list (title, counts). */
  repaint(): void;
  /** Focus the detail pane's title or date field for the selected task. */
  focusDetail(what: 'due' | 'title'): void;
}

interface UndoStep { label: string; run(): Promise<unknown> }

export function createTaskList(host: ListHost) {
  const { el, state } = host;
  let rows: Row[] = [];
  let matches = 0;
  const undo: UndoStep[] = [];

  const taskRows = () => rows.filter((r): r is TaskRow => r.type === 'task');
  const selected = (): string[] => taskRows().map((r) => r.task.id).filter((id) => state.sel.has(id));

  /* ------------------------------------------------------------- painting */
  function compute(): void {
    const res = buildRows([...td.tasks.values()], td.refs, {
      view: state.view, query: state.query, sort: state.sort, group: state.group, showDone: state.showDone,
      flat: state.flat, folded: state.folded, note: state.note, keep: state.keep,
    });
    rows = res.rows;
    matches = res.matches;
    // forget selected tasks that are gone
    for (const id of [...state.sel]) if (!td.tasks.has(id)) state.sel.delete(id);
  }

  function rowHtml(r: Row): string {
    if (r.type === 'group') {
      const lead = r.note !== undefined ? icon(r.note ? 'doc' : 'inbox') : '';
      const name = r.note ? `<a href="#" data-open-note="${escapeHtml(r.note)}" title="Open the note">${escapeHtml(r.label)}</a>` : escapeHtml(r.label);
      return `<div class="tv-group ${r.state || ''}" role="presentation">${lead}<span>${name}</span><span class="n">${r.count || ''}</span></div>`;
    }
    const t = r.task;
    const ref = (td.refs[t.id] || [])[0];
    const where = state.group !== 'note' && r.depth === 0 && ref
      ? `<span class="tv-where" title="${escapeHtml(ref.path)}">${escapeHtml(ref.title || ref.path)}${ref.heading && ref.heading !== ref.title ? ` › ${escapeHtml(ref.heading)}` : ''}</span>` : '';
    const twisty = r.kids && !state.flat ? `<button type="button" class="tv-twisty${r.folded ? '' : ' open'}" data-act="fold" tabindex="-1" aria-label="${r.folded ? 'Unfold' : 'Fold'}">${icon('chevron', 'icon-sm')}</button>` : '<span class="tv-twisty"></span>';
    return `<div class="tv-row${t.status === 'done' ? ' done' : ''}${state.sel.has(t.id) ? ' selected' : ''}${state.cursor === t.id ? ' cursor' : ''}${r.context ? ' context' : ''}"
        role="treeitem" aria-level="${r.depth + 1}" aria-selected="${state.sel.has(t.id)}" ${r.kids ? `aria-expanded="${!r.folded}"` : ''}
        data-id="${t.id}" draggable="true" style="--depth:${r.depth}">
        ${twisty}${boxHtml(t, 'data-act="toggle"')}<span class="tv-title">${titleHtml(t.title)}</span>${chipsHtml(t)}${where}</div>`;
  }

  function paint(): void {
    compute();
    const empty = td.loaded
      ? `<div class="empty-state tv-empty">${icon('tasks')}<h3>${state.query ? 'No tasks match' : 'Nothing here'}</h3>
          <p>${state.query ? 'Try other words, or clear the search.' : `Add a task above, or in a note with ${modLabel}↩ on a line.`}</p></div>`
      : '<div class="tv-loading muted">Loading tasks…</div>';
    const scroll = el.scrollTop;
    el.innerHTML = rows.length ? rows.map(rowHtml).join('') : empty;
    el.scrollTop = scroll;
    host.repaint();
  }

  /* ------------------------------------------------------------- selection */
  function select(id: string, { extend = false, toggle = false } = {}): void {
    const ids = taskRows().map((r) => r.task.id);
    if (extend && state.anchor && ids.includes(state.anchor)) {
      const a = ids.indexOf(state.anchor), b = ids.indexOf(id);
      state.sel = new Set(ids.slice(Math.min(a, b), Math.max(a, b) + 1));
    } else if (toggle) {
      if (state.sel.has(id)) state.sel.delete(id); else state.sel.add(id);
      state.anchor = id;
    } else {
      state.sel = new Set([id]);
      state.anchor = id;
    }
    state.cursor = id;
    paintSelection();
    host.onSelect();
  }
  function paintSelection(): void {
    el.querySelectorAll<HTMLElement>('.tv-row').forEach((r) => {
      const on = state.sel.has(r.dataset.id!);
      r.classList.toggle('selected', on);
      r.setAttribute('aria-selected', String(on));
      r.classList.toggle('cursor', r.dataset.id === state.cursor);
    });
    el.querySelector('.tv-row.cursor')?.scrollIntoView({ block: 'nearest' });
  }
  function move(delta: number, extend: boolean): void {
    const ids = taskRows().map((r) => r.task.id);
    if (!ids.length) return;
    const i = state.cursor ? ids.indexOf(state.cursor) : -1;
    const next = ids[Math.max(0, Math.min(ids.length - 1, i < 0 ? 0 : i + delta))];
    select(next, { extend });
  }

  /* ------------------------------------------------------------- changes with undo */
  function remember(step: UndoStep): void { undo.push(step); if (undo.length > 50) undo.shift(); }

  async function change(ids: string[], changes: TaskChanges, label: string): Promise<void> {
    const before = ids.map((id) => td.tasks.get(id)).filter((t): t is Task => !!t);
    await Promise.all(before.map((t) => patchTask(t.id, changes)));
    remember({ label, run: () => Promise.all(before.map((t) => {
      const back: TaskChanges = {};
      for (const k of Object.keys(changes) as (keyof TaskChanges)[]) if (k in t) (back as any)[k] = (t as any)[k];
      return patchTask(t.id, back);
    })) });
  }

  async function toggleSelected(): Promise<void> {
    const ids = selected();
    for (const id of ids) state.keep.add(id);
    if (ids.length === 1) { await toggleTask(ids[0]); return; }
    const allDone = ids.every((id) => td.tasks.get(id)?.status === 'done');
    await change(ids, { status: allDone ? 'open' : 'done' }, allDone ? 'Reopen' : 'Mark done');
  }

  async function removeSelected(): Promise<void> {
    const ids = selected();
    if (!ids.length) return;
    const top = ids.filter((id) => { const p = td.tasks.get(id)?.parent_id; return !p || !ids.includes(p); });
    const cur = state.cursor;
    const list = taskRows().map((r) => r.task.id);
    const next = list.slice(list.indexOf(cur || '') + 1).find((x) => !ids.includes(x)) || list.slice(0, list.indexOf(cur || '')).reverse().find((x) => !ids.includes(x));
    try {
      const res = await api.deleteTasks(top);
      await loadTasks(true);
      state.sel = new Set(next ? [next] : []);
      state.cursor = next || null;
      host.onSelect();
      const n = res.ids.length;
      remember({ label: 'Delete', run: () => restoreBatch(res.batch) });
      toast(`Deleted ${plural(n, 'task')}.`, { action: { label: 'Undo', run: () => restoreBatch(res.batch) }, timeout: 8000 });
    } catch (e) { toastError(e); }
  }

  /** Put a task under ``parent`` (null: top level), before or after a sibling. */
  async function moveTask(id: string, to: { parent_id: string | null; before?: string | null; after?: string | null }): Promise<void> {
    const t = td.tasks.get(id);
    if (!t) return;
    const sibs = [...td.tasks.values()].filter((x) => x.parent_id === t.parent_id && x.id !== id).sort((a, b) => a.position - b.position);
    const next = sibs.find((x) => x.position > t.position);
    const res = await patchTask(id, to);
    if (res) remember({ label: 'Move', run: () => patchTask(id, { parent_id: t.parent_id, before: next?.id || null }) });
  }

  async function indent(dir: 1 | -1): Promise<void> {
    const id = state.cursor;
    const t = id ? td.tasks.get(id) : undefined;
    if (!t) return;
    const sibs = [...td.tasks.values()].filter((x) => x.parent_id === t.parent_id).sort((a, b) => a.position - b.position);
    if (dir === 1) {
      const prev = sibs[sibs.indexOf(t) - 1];
      if (!prev) return;
      state.folded.delete(prev.id);
      await moveTask(t.id, { parent_id: prev.id });
    } else {
      if (!t.parent_id) return;
      const parent = td.tasks.get(t.parent_id);
      await moveTask(t.id, { parent_id: parent?.parent_id ?? null, after: parent?.id ?? null });
    }
  }

  async function reorder(dir: 1 | -1): Promise<void> {
    const t = state.cursor ? td.tasks.get(state.cursor) : undefined;
    if (!t) return;
    const sibs = [...td.tasks.values()].filter((x) => x.parent_id === t.parent_id).sort((a, b) => a.position - b.position);
    const other = sibs[sibs.indexOf(t) + dir];
    if (!other) return;
    await moveTask(t.id, dir === 1 ? { parent_id: t.parent_id, after: other.id } : { parent_id: t.parent_id, before: other.id });
  }

  /** A new task right below the cursor (as its sibling, or as its sub-task), typed in place. */
  function addBelow(asChild: boolean): void {
    const t = state.cursor ? td.tasks.get(state.cursor) : undefined;
    const row = t ? el.querySelector<HTMLElement>(`.tv-row[data-id="${t.id}"]`) : null;
    const depth = row ? Number(row.style.getPropertyValue('--depth')) + (asChild ? 1 : 0) : 0;
    const input = document.createElement('div');
    input.className = 'tv-row tv-new';
    input.style.setProperty('--depth', String(depth));
    input.innerHTML = `<span class="tv-twisty"></span><span class="task-check ghost"></span><input class="tv-new-input" placeholder="New task (a date at the end: fri, 27 oct); Enter adds, Esc cancels" spellcheck="false">`;
    if (row) row.after(input); else el.prepend(input);
    const box = input.querySelector('input')!;
    box.focus();
    let done = false;
    const finish = async (keep: boolean) => {
      if (done) return;
      done = true;
      const text = box.value.trim();
      input.remove();
      if (!keep || !text) { el.focus(); return; }
      const q = parseQuick(text);
      const data: TaskChanges = { title: q.title || text, due: q.due };
      if (t && asChild) data.parent_id = t.id;
      else if (t) { data.parent_id = t.parent_id; data.after = t.id; }
      if (asChild && t) state.folded.delete(t.id);
      const made = await createTask(data);
      if (made) {
        remember({ label: 'Add', run: () => deleteTasks([made.id]) });
        state.sel = new Set([made.id]);
        state.cursor = made.id;
        paint();
        host.onSelect();
      }
      el.focus();
    };
    box.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); finish(true).then(() => { if (box.value.trim()) addBelow(false); }); }
      if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    });
    box.addEventListener('blur', () => finish(true));
  }

  /** Rename in place (double-click or Enter). */
  function rename(id: string): void {
    const row = el.querySelector<HTMLElement>(`.tv-row[data-id="${id}"] .tv-title`);
    const t = td.tasks.get(id);
    if (!row || !t) return;
    row.innerHTML = '<input class="tv-rename" spellcheck="true" aria-label="Title">';
    const input = row.querySelector('input')!;
    input.value = t.title;
    input.focus();
    input.select();
    let done = false;
    const finish = (keep: boolean) => {
      if (done) return;
      done = true;
      const v = input.value.replace(/\s+/g, ' ').trim();
      if (keep && v && v !== t.title) change([id], { title: v }, 'Rename');
      else paint();
      el.focus();
    };
    input.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
  }

  async function showInNote(ids: string[]): Promise<void> {
    const path = await pickNote(ids.length > 1 ? `Show ${ids.length} tasks in a note` : 'Show the task in a note');
    if (!path) return;
    try {
      for (const id of ids) await api.addTaskToNote(id, path);
      await loadTasks(true);
      toast(`Added to the note.`, { type: 'success', action: { label: 'Open', run: () => openNoteAt(path) } });
    } catch (e) { toastError(e); }
  }

  function openInNote(id: string): void {
    for (let t: Task | undefined = td.tasks.get(id); t; t = t.parent_id ? td.tasks.get(t.parent_id) : undefined) {
      const r = td.refs[t.id]?.[0];
      if (r) { openNoteAt(r.path, r.line); return; }
    }
    toast('This task is not in a note yet.', { action: { label: 'Show in a note…', run: () => showInNote([id]) } });
  }

  function rowMenu(id: string, at: { x: number; y: number } | Element): void {
    if (!state.sel.has(id)) select(id);
    const ids = selected();
    const one = ids.length === 1 ? td.tasks.get(ids[0]) : undefined;
    const due = (v: string | null, label: string) => ({ label, icon: 'calendar', run: () => change(ids, { due: v }, 'Due date') });
    openMenu([
      one ? { label: 'Rename', icon: 'edit', shortcut: '↩', run: () => rename(one.id) } : null,
      { label: ids.every((x) => td.tasks.get(x)?.status === 'done') ? 'Mark as open' : 'Mark as done', icon: 'check', shortcut: 'Space', run: toggleSelected },
      'sep',
      { section: 'Due date' },
      due(parseDue('today'), 'Today'), due(parseDue('tomorrow'), 'Tomorrow'), due(parseDue('next week'), 'Next week (Monday)'),
      one ? { label: 'Other date…', icon: 'calendar', shortcut: 'D', run: () => host.focusDetail('due') } : null,
      due(null, 'No date'),
      'sep',
      one ? { label: 'Add task below', icon: 'plus', shortcut: 'N', run: () => addBelow(false) } : null,
      one ? { label: 'Add sub-task', icon: 'subtask', shortcut: '⇧N', run: () => addBelow(true) } : null,
      one ? { label: 'Indent', icon: 'indent', shortcut: 'Tab', run: () => indent(1) } : null,
      one && one.parent_id ? { label: 'Outdent', icon: 'outdent', shortcut: '⇧Tab', run: () => indent(-1) } : null,
      'sep',
      one ? { label: 'Open in note', icon: 'open', shortcut: 'O', run: () => openInNote(one.id) } : null,
      { label: 'Show in a note…', icon: 'doc', run: () => showInNote(ids) },
      'sep',
      { label: ids.length > 1 ? `Delete ${ids.length} tasks` : 'Delete', icon: 'trash', danger: true, shortcut: '⌫', run: removeSelected },
    ], at);
  }

  /* ------------------------------------------------------------- events */
  el.addEventListener('mousedown', (e) => {
    const row = (e.target as Element).closest<HTMLElement>('.tv-row[data-id]');
    if (!row || (e.target as Element).closest('input')) return;
    if ((e.target as Element).closest('[data-act]')) return;   // the box and the twisty do their own thing
    select(row.dataset.id!, { extend: e.shiftKey, toggle: modKey(e) });
  });
  el.addEventListener('click', (e) => {
    const t = e.target as Element;
    const note = t.closest<HTMLElement>('[data-open-note]');
    if (note) { e.preventDefault(); openNoteAt(note.dataset.openNote!); return; }
    const row = t.closest<HTMLElement>('.tv-row[data-id]');
    if (!row) return;
    const id = row.dataset.id!;
    const act = t.closest<HTMLElement>('[data-act]')?.dataset.act;
    if (act === 'toggle') { e.preventDefault(); state.keep.add(id); if (state.sel.has(id) && state.sel.size > 1) toggleSelected(); else toggleTask(id); return; }
    if (act === 'fold') { if (state.folded.has(id)) state.folded.delete(id); else state.folded.add(id); state.save(); paint(); return; }
    if (t.closest('a.wikilink')) { e.preventDefault(); const a = t.closest<HTMLElement>('a.wikilink')!; if (a.dataset.path) openNoteAt(a.dataset.path); }
  });
  el.addEventListener('dblclick', (e) => {
    const row = (e.target as Element).closest<HTMLElement>('.tv-row[data-id]');
    if (row && !(e.target as Element).closest('[data-act], input')) rename(row.dataset.id!);
  });
  el.addEventListener('contextmenu', (e) => {
    const row = (e.target as Element).closest<HTMLElement>('.tv-row[data-id]');
    if (!row) return;
    e.preventDefault();
    rowMenu(row.dataset.id!, { x: e.clientX, y: e.clientY });
  });
  el.addEventListener('keydown', (e) => {
    if ((e.target as Element).closest('input, textarea')) return;
    const k = e.key;
    const mod = modKey(e);
    const cur = state.cursor;
    let handled = true;
    if (k === 'ArrowDown' && e.altKey) reorder(1);
    else if (k === 'ArrowUp' && e.altKey) reorder(-1);
    else if (k === 'ArrowDown') move(1, e.shiftKey);
    else if (k === 'ArrowUp') move(-1, e.shiftKey);
    else if (k === 'Home') move(-1e6, e.shiftKey);
    else if (k === 'End') move(1e6, e.shiftKey);
    else if (k === 'ArrowRight' && cur) {
      const r = taskRows().find((x) => x.task.id === cur);
      if (r?.kids && r.folded) { state.folded.delete(cur); state.save(); paint(); } else move(1, false);
    } else if (k === 'ArrowLeft' && cur) {
      const r = taskRows().find((x) => x.task.id === cur);
      if (r?.kids && !r.folded) { state.folded.add(cur); state.save(); paint(); }
      else if (r?.task.parent_id && td.tasks.has(r.task.parent_id)) select(r.task.parent_id);
    } else if (k === ' ' && cur) toggleSelected();
    else if ((k === 'Enter' || k === 'F2') && cur && !mod) rename(cur);
    else if ((k === 'Delete' || k === 'Backspace') && state.sel.size) removeSelected();
    else if (k === 'Tab') indent(e.shiftKey ? -1 : 1);
    else if (mod && k.toLowerCase() === 'a') { state.sel = new Set(taskRows().map((r) => r.task.id)); paintSelection(); host.onSelect(); }
    else if (mod && k.toLowerCase() === 'z') undoLast();
    else if (!mod && !e.altKey && k.toLowerCase() === 'n') addBelow(e.shiftKey);
    else if (!mod && !e.altKey && k.toLowerCase() === 'o' && cur) openInNote(cur);
    else if (!mod && !e.altKey && k.toLowerCase() === 'd' && cur) host.focusDetail('due');
    else if (k === 'Escape' && state.sel.size > 1 && cur) select(cur);
    else handled = false;
    if (handled) e.preventDefault();
  });

  // drag and drop: onto the top or bottom edge of a row puts the task before or after it, onto its
  // middle makes it a sub-task; onto a note in the views column shows it in that note
  let dragging: string | null = null;
  el.addEventListener('dragstart', (e) => {
    const row = (e.target as Element).closest<HTMLElement>('.tv-row[data-id]');
    if (!row) return;
    dragging = row.dataset.id!;
    e.dataTransfer!.effectAllowed = 'move';
    e.dataTransfer!.setData('application/x-synapse-task', dragging);
    e.dataTransfer!.setData('text/plain', `{{task:${dragging}}}`);
    row.classList.add('dragging');
  });
  el.addEventListener('dragend', () => { dragging = null; el.querySelectorAll('.dragging, .drop-before, .drop-after, .drop-into').forEach((x) => x.classList.remove('dragging', 'drop-before', 'drop-after', 'drop-into')); });
  const zone = (e: DragEvent, row: HTMLElement) => {
    const r = row.getBoundingClientRect();
    const f = (e.clientY - r.top) / r.height;
    return f < 0.28 ? 'before' : f > 0.72 ? 'after' : 'into';
  };
  el.addEventListener('dragover', (e) => {
    const row = (e.target as Element).closest<HTMLElement>('.tv-row[data-id]');
    if (!dragging || !row || row.dataset.id === dragging) return;
    e.preventDefault();
    el.querySelectorAll('.drop-before, .drop-after, .drop-into').forEach((x) => x.classList.remove('drop-before', 'drop-after', 'drop-into'));
    row.classList.add(`drop-${zone(e, row)}`);
  });
  el.addEventListener('drop', (e) => {
    const row = (e.target as Element).closest<HTMLElement>('.tv-row[data-id]');
    if (!dragging || !row || row.dataset.id === dragging) return;
    e.preventDefault();
    const target = td.tasks.get(row.dataset.id!);
    const id = dragging;
    const z = zone(e, row);
    row.classList.remove('drop-before', 'drop-after', 'drop-into');
    if (!target) return;
    for (let p: Task | undefined = target; p; p = p.parent_id ? td.tasks.get(p.parent_id) : undefined) {
      if (p.id === id) { toast('A task cannot go under one of its own sub-tasks.'); return; }
    }
    if (z === 'into') { state.folded.delete(target.id); moveTask(id, { parent_id: target.id }); }
    else moveTask(id, { parent_id: target.parent_id, [z]: target.id });
  });

  async function undoLast(): Promise<void> {
    const step = undo.pop();
    if (!step) { toast('Nothing to undo.'); return; }
    try { await step.run(); toast(`Undid: ${step.label}.`); } catch (e) { toastError(e); }
  }

  return {
    paint,
    paintSelection,
    select,
    selected,
    count: () => matches,
    rows: () => rows,
    addBelow,
    rowMenu,
    showInNote,
    removeSelected,
    change,
    remember,
    undoLast,
    dragging: () => dragging,
  };
}

export type TaskList = ReturnType<typeof createTaskList>;
