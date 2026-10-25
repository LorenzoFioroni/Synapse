// Everything about one task, editable: title, status, due date, the Markdown body
// (the notes editor, with live preview), sub-tasks and the notes that show it. Used by the Tasks
// page's detail pane and by the popup that opens from a task in a note.
import { api } from '../../../api.js';
import { icon } from '../../../icons.js';
import { off, on } from '../../../store.js';
import type { Task } from '../../../types.js';
import { openMenu } from '../../../ui/menu.js';
import { toast, toastError } from '../../../ui/toast.js';
import { aiName, aiOwner, escapeHtml, formatDate } from '../../../util.js';
import { type NoteEditor, createEditor } from '../../../views/notes/editor.js';
import { renderMermaid } from '../../../views/notes/richRender.js';
import { citeInfo, equationMode, noteMacros, taskHooks, ws } from '../../../views/notes/workspace.js';
import { dueLabel, dueState, isoDate, monthGrid, monthName, parseDue, parseQuick, todayIso } from './dates.js';
import { openNoteAt, pickNote } from './notePicker.js';
import {
  childrenOf, createTask, deleteTasks, loadTasks, patchTask, restoreTask, td, toggleTask,
} from './taskData.js';
import { boxHtml, chipsHtml, ensureRenderer, resolveAnywhere, taskRenderer, titleHtml } from './taskRender.js';

export interface TaskFormOptions {
  /** Show another task here (a sub-task or a parent clicked). */
  onOpenTask(id: string): void;
  /** The form sits in a popup in a note (more compact; offers "Open in Tasks"). */
  popup?: boolean;
  onOpenInTasks?(id: string): void;
  onClose?(): void;
}

export interface TaskForm { show(id: string): void; id(): string | null; destroy(): void; focusTitle(): void }

export function mountTaskForm(root: HTMLElement, opts: TaskFormOptions): TaskForm {
  let id: string | null = null;
  let editor: NoteEditor | null = null;
  let bodySent = '';          // the body as last sent to (or received from) the server
  let editingTitle = false;
  let destroyed = false;

  root.classList.add('task-form');
  root.innerHTML = `
    <div class="tf-crumbs"></div>
    <div class="tf-head"><span class="tf-box"></span><div class="tf-title" tabindex="0" title="Click to edit"></div></div>
    <div class="tf-props">
      <span class="tf-label">Status</span><div class="tf-status segmented tf-seg" role="group"></div>
      <span class="tf-label">Due</span><div class="tf-due"><div class="tf-date">
        ${icon('calendar')}<input class="tf-date-input" placeholder="Add a date: fri, 27 oct, in 3 days…" spellcheck="false" aria-label="Due date">
        <span class="tf-date-hint"></span><button type="button" class="icon-btn tf-date-clear" title="No due date">${icon('close', 'icon-sm')}</button></div>
        <button type="button" class="icon-btn tf-cal-btn" title="Pick a date">${icon('calendar')}</button></div>
    </div>
    <div class="tf-section"><span>Details</span><div class="segmented tf-seg tf-modes">
      <button data-tf-mode="live" class="active">Live</button><button data-tf-mode="source">Source</button></div></div>
    <div class="tf-body"><div class="tf-editor"></div></div>
    <div class="tf-section"><span>Sub-tasks</span><span class="tf-sub-count"></span></div>
    <div class="tf-subs"></div>
    <div class="tf-add-sub"><span class="task-check ghost"></span><input placeholder="Add a sub-task…" spellcheck="false" aria-label="Add a sub-task"></div>
    <div class="tf-section"><span>In notes</span></div>
    <div class="tf-refs"></div>
    <div class="tf-ai" hidden></div>
    <div class="tf-meta muted"></div>
    <div class="tf-foot"></div>`;
  const $ = <T extends HTMLElement = HTMLElement>(s: string) => root.querySelector<T>(s)!;
  const task = (): Task | undefined => (id ? td.tasks.get(id) : undefined);

  /* ------------------------------------------------------------- painting */
  function paint(): void {
    if (destroyed) return;
    const t = task();
    if (!t) {
      root.classList.add('tf-missing');
      $('.tf-title').innerHTML = '<span class="muted">This task no longer exists (deleted, or not loaded yet).</span>';
      return;
    }
    root.classList.remove('tf-missing');
    root.classList.toggle('done', t.status === 'done');
    // crumbs: the parents
    const chain: Task[] = [];
    for (let p = t.parent_id ? td.tasks.get(t.parent_id) : undefined; p; p = p.parent_id ? td.tasks.get(p.parent_id) : undefined) chain.unshift(p);
    $('.tf-crumbs').innerHTML = chain.length
      ? chain.map((p) => `<a href="#" data-tf-open="${p.id}">${titleHtml(p.title)}</a>`).join('<span class="sep">›</span>') + '<span class="sep">›</span>'
      : '';
    $('.tf-box').innerHTML = boxHtml(t, 'data-tf="toggle"');
    if (!editingTitle) $('.tf-title').innerHTML = titleHtml(t.title);
    $('.tf-status').innerHTML = (['open', 'done'] as const).map((s) => `<button data-tf-status="${s}" class="${t.status === s ? 'active' : ''}">${s === 'open' ? 'Open' : 'Done'}</button>`).join('');
    const dateInput = $<HTMLInputElement>('.tf-date-input');
    if (document.activeElement !== dateInput) {
      dateInput.value = t.due ? dueLabel(t.due) : '';
      $('.tf-date').className = `tf-date ${t.status === 'done' ? '' : dueState(t.due)}`;
      $('.tf-date-hint').textContent = t.due ? t.due : '';
    }
    $('.tf-date-clear').hidden = !t.due;
    // sub-tasks
    const kids = childrenOf(t.id);
    $('.tf-sub-count').textContent = kids.length ? `${kids.filter((k) => k.status === 'done').length}/${kids.length}` : '';
    $('.tf-subs').innerHTML = kids.map((k) => `<div class="tf-sub${k.status === 'done' ? ' done' : ''}" data-tf-sub="${k.id}">
        ${boxHtml(k, 'data-tf="toggle-sub"')}<span class="tf-sub-title" data-tf-open="${k.id}">${titleHtml(k.title)}</span>${chipsHtml(k)}</div>`).join('');
    // notes
    const refs = td.refs[t.id] || [];
    $('.tf-refs').innerHTML = refs.map((r) => `<div class="tf-ref"><a href="#" data-tf-note="${escapeHtml(r.path)}" data-tf-line="${r.line}">${icon('doc')}<span>${escapeHtml(r.title || r.path)}${r.heading && r.heading !== r.title ? ` <span class="muted">› ${escapeHtml(r.heading)}</span>` : ''}</span></a>
        <button type="button" class="icon-btn" data-tf-unref="${escapeHtml(r.path)}" title="Take it out of this note (the task stays)">${icon('close', 'icon-sm')}</button></div>`).join('')
      + `<button type="button" class="link tf-add-ref" data-tf="add-ref">${icon('plus', 'icon-sm')} Show in a note…</button>`;
    // an AI app's last change
    const ai = t.updated_by === 'ai';
    $('.tf-ai').hidden = !ai;
    if (ai) $('.tf-ai').innerHTML = `${icon('sparkle')} <span>${escapeHtml(aiName(t.updated_agent))} changed this task.</span> <button type="button" class="link" data-tf="ai-undo">Undo</button>`;
    $('.tf-meta').textContent = `Created ${formatDate(t.created_at)}${t.done_at ? ` · done ${formatDate(t.done_at)}` : ''}`;
    $('.tf-foot').innerHTML = `${opts.popup ? `<button type="button" class="btn" data-tf="in-tasks">${icon('tasks')} Open in Tasks</button>` : ''}
      ${!opts.popup && refs.length ? `<button type="button" class="btn" data-tf="open-note">${icon('open')} Open in note</button>` : ''}
      <span class="spacer"></span><button type="button" class="btn btn-ghost btn-danger" data-tf="delete">${icon('trash')} Delete</button>`;
    paintBody(t);
  }

  let making: Promise<NoteEditor> | null = null;   // one editor, even when painted again while it loads
  async function paintBody(t: Task): Promise<void> {
    if (!editor) {
      making ||= createEditor($('.tf-editor'), {
        onChange: (text) => { if (id) saveBody(id, text); root.querySelector('.tf-body')!.classList.toggle('empty', !text.trim()); },
        onBlur: () => saveBody.flush(),
        onImage: async (file) => { try { return (await api.uploadNoteImage(file)).markdown; } catch (e) { toastError(e); return null; } },
      }, {
        renderer: taskRenderer,
        resolveNote: resolveAnywhere,
        citeInfo,
        openNote: (target) => { const p = resolveAnywhere(target); if (p) openNoteAt(p); },
        mermaid: renderMermaid,
        equations: equationMode,
        macros: noteMacros,
        task: () => taskHooks.preview,
      }, 'live', { items: () => ws.library.list, notes: () => ws.overview.notes, tasks: () => taskHooks.choices?.() ?? [], taskHtml: (tid) => taskHooks.titleHtml?.(tid) ?? '' });
      const made = await making;
      if (destroyed) { made.destroy(); return; }
      if (!editor) { editor = made; editor.view.contentDOM.setAttribute('aria-label', 'Details'); }
      t = task() || t;
    }
    if (!editor) return;
    const cur = editor.text();
    if (t.body !== bodySent && t.body !== cur && !editor.view.hasFocus) {
      bodySent = t.body;
      editor.load(t.body);
    }
    root.querySelector('.tf-body')!.classList.toggle('empty', !editor.text().trim());
  }

  // the body is saved 0.7 s after the last keystroke, and at once when leaving the editor or the task
  let pending: { id: string; text: string } | null = null;
  let bodyTimer: ReturnType<typeof setTimeout> | undefined;
  const sendBody = () => {
    clearTimeout(bodyTimer);
    const p = pending;
    pending = null;
    if (!p || p.text === bodySent) return;
    bodySent = p.text;
    patchTask(p.id, { body: p.text });
  };
  const saveBody = Object.assign((tid: string, text: string) => {
    pending = { id: tid, text };
    clearTimeout(bodyTimer);
    bodyTimer = setTimeout(sendBody, 700);
  }, { flush: sendBody });

  /* ------------------------------------------------------------- editing */
  function editTitle(): void {
    const t = task();
    if (!t || editingTitle) return;
    editingTitle = true;
    const el = $('.tf-title');
    el.innerHTML = `<textarea class="tf-title-input" rows="1" spellcheck="true" aria-label="Title"></textarea>`;
    const ta = el.querySelector('textarea')!;
    ta.value = t.title;
    const fit = () => { ta.style.height = 'auto'; ta.style.height = `${ta.scrollHeight}px`; };
    fit();
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    let cancelled = false;
    ta.addEventListener('input', fit);
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); ta.blur(); }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancelled = true; ta.blur(); }
    });
    ta.addEventListener('blur', () => {
      editingTitle = false;
      const v = ta.value.replace(/\s+/g, ' ').trim();
      if (!cancelled && v && v !== t.title && id) patchTask(id, { title: v }); else paint();
    }, { once: true });
  }

  function setDue(text: string): void {
    const t = task();
    if (!t || !id) return;
    const v = text.trim();
    if (!v) { if (t.due) patchTask(id, { due: null }); else paint(); return; }
    const d = parseDue(v);
    if (!d) { $('.tf-date').classList.add('invalid'); $('.tf-date-hint').textContent = 'not a date'; return; }
    if (d !== t.due) patchTask(id, { due: d }); else paint();
  }

  function calendar(anchor: HTMLElement): void {
    const t = task();
    if (!t || !id) return;
    const tid = id;
    const start = t.due ? new Date(`${t.due}T12:00:00`) : new Date();
    let y = start.getFullYear(), m = start.getMonth();
    const pop = document.createElement('div');
    pop.className = 'menu tf-calendar';
    const paintCal = () => {
      const today = todayIso();
      pop.innerHTML = `<div class="cal-head"><button type="button" class="icon-btn" data-cal="-1">${icon('chevron', 'icon-sm flip')}</button>
        <span>${monthName(m)} ${y}</span><button type="button" class="icon-btn" data-cal="1">${icon('chevron', 'icon-sm')}</button></div>
        <div class="cal-grid">${['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map((d) => `<span class="cal-dow">${d}</span>`).join('')}
        ${monthGrid(y, m).map((c) => `<button type="button" class="cal-day${c.inMonth ? '' : ' out'}${c.iso === today ? ' today' : ''}${c.iso === task()?.due ? ' picked' : ''}" data-day="${c.iso}">${c.day}</button>`).join('')}</div>
        <div class="cal-quick">${[['Today', 0], ['Tomorrow', 1], ['Next week', 'next week']].map(([l, v]) => `<button type="button" class="btn btn-sm" data-quick="${v}">${l}</button>`).join('')}
        <button type="button" class="btn btn-sm btn-ghost" data-quick="">No date</button></div>`;
    };
    paintCal();
    pop.addEventListener('mousedown', (e) => e.preventDefault());
    pop.addEventListener('click', (e) => {
      const b = (e.target as Element).closest<HTMLElement>('button');
      if (!b) return;
      if (b.dataset.cal) { m += Number(b.dataset.cal); if (m < 0) { m = 11; y--; } if (m > 11) { m = 0; y++; } paintCal(); return; }
      let v: string | null = b.dataset.day || null;
      if (b.dataset.quick !== undefined) {
        const q = b.dataset.quick;
        v = q === '' ? null : /^\d+$/.test(q) ? isoDate(new Date(Date.now() + Number(q) * 864e5)) : parseDue(q);
      }
      patchTask(tid, { due: v });
      close();
    });
    const close = () => { pop.remove(); document.removeEventListener('pointerdown', outside, true); };
    const outside = (e: Event) => { if (!pop.contains(e.target as Node) && e.target !== anchor) close(); };
    document.body.appendChild(pop);
    const r = anchor.getBoundingClientRect();
    pop.style.left = `${Math.max(8, Math.min(innerWidth - pop.offsetWidth - 8, r.right - pop.offsetWidth))}px`;
    pop.style.top = `${r.bottom + 4 + pop.offsetHeight > innerHeight ? r.top - pop.offsetHeight - 4 : r.bottom + 4}px`;
    setTimeout(() => document.addEventListener('pointerdown', outside, true), 0);
  }

  async function addSub(input: HTMLInputElement): Promise<void> {
    const t = task();
    const text = input.value.trim();
    if (!t || !text) return;
    const q = parseQuick(text);
    input.value = '';
    await createTask({ title: q.title || text, due: q.due, parent_id: t.id });
    input.focus();
  }

  async function undoAi(): Promise<void> {
    if (!id) return;
    try {
      const h = (await api.taskHistory(id)).history.find((x) => x.by === 'ai' && !x.undone && x.action !== 'restore');
      if (!h) { toast('No AI change left to undo.'); return; }
      await api.undoTaskChange(h.id);
      await loadTasks(true);
      toast(`Undid ${aiOwner(h.agent)} change.`, { type: 'success' });
    } catch (e) { toastError(e); }
  }

  /* ------------------------------------------------------------- events */
  root.addEventListener('click', async (e) => {
    const el = e.target as Element;
    const t = task();
    const a = el.closest<HTMLElement>('[data-tf], [data-tf-open], [data-tf-status], [data-tf-note], [data-tf-unref], [data-tf-mode]');
    if (!a) { if (el.closest('.tf-title') && !editingTitle && !el.closest('a, .cite, .wikilink')) editTitle(); return; }
    e.preventDefault();
    const d = a.dataset;
    if (!t || !id) return;
    if (d.tf === 'toggle') toggleTask(id);
    else if (d.tf === 'toggle-sub') { const s = a.closest<HTMLElement>('[data-tf-sub]')?.dataset.tfSub; if (s) toggleTask(s); }
    else if (d.tfOpen) opts.onOpenTask(d.tfOpen);
    else if (d.tfStatus) { if (d.tfStatus !== t.status) toggleTask(id); }
    else if (d.tfNote) { opts.onClose?.(); openNoteAt(d.tfNote, Number(d.tfLine) || null); }
    else if (d.tfUnref) {
      try { await api.removeTaskFromNote(id, d.tfUnref); await loadTasks(true); } catch (err) { toastError(err); }
    } else if (d.tfMode) {
      root.querySelectorAll<HTMLElement>('[data-tf-mode]').forEach((b) => b.classList.toggle('active', b === a));
      editor?.setMode(d.tfMode as 'live' | 'source');
    } else if (d.tf === 'add-ref') {
      const path = await pickNote();
      if (!path) return;
      try { await api.addTaskToNote(id, path); await loadTasks(true); toast('Added to the note.', { type: 'success' }); } catch (err) { toastError(err); }
    } else if (d.tf === 'ai-undo') undoAi();
    else if (d.tf === 'in-tasks') { opts.onClose?.(); opts.onOpenInTasks?.(id); }
    else if (d.tf === 'open-note') {
      const refs = td.refs[id] || [];
      if (refs.length === 1) { opts.onClose?.(); openNoteAt(refs[0].path, refs[0].line); }
      else openMenu(refs.map((r) => ({ label: r.title || r.path, icon: 'doc', run: () => { opts.onClose?.(); openNoteAt(r.path, r.line); } })), a);
    } else if (d.tf === 'delete') {
      const del = id;
      opts.onClose?.();
      deleteTasks([del]);
    }
  });
  root.addEventListener('keydown', (e) => {
    const el = e.target as HTMLElement;
    if (el.classList.contains('tf-title') && (e.key === 'Enter' || e.key === 'F2')) { e.preventDefault(); editTitle(); }
    if (el.classList.contains('tf-date-input')) {
      if (e.key === 'Enter') { e.preventDefault(); setDue((el as HTMLInputElement).value); (el as HTMLInputElement).blur(); }
      if (e.key === 'Escape') { e.stopPropagation(); (el as HTMLInputElement).blur(); paint(); }
    }
    if (el.closest('.tf-add-sub') && e.key === 'Enter') { e.preventDefault(); addSub(el as HTMLInputElement); }
  });
  root.addEventListener('input', (e) => {
    const el = e.target as HTMLInputElement;
    if (el.classList.contains('tf-date-input')) {
      const v = el.value.trim();
      const d = v ? parseDue(v) : null;
      $('.tf-date').classList.toggle('invalid', !!v && !d);
      $('.tf-date-hint').textContent = !v ? '' : d ? `→ ${dueLabel(d)}${d !== el.value ? ` (${d})` : ''}` : 'not a date';
    }
  });
  root.addEventListener('focusout', (e) => {
    const el = e.target as HTMLInputElement;
    if (el.classList?.contains('tf-date-input')) setDue(el.value);
  });
  root.addEventListener('focusin', (e) => {
    const el = e.target as HTMLInputElement;
    if (el.classList?.contains('tf-date-input')) { el.value = task()?.due || ''; el.select(); }
  });
  $('.tf-cal-btn').addEventListener('click', (e) => calendar(e.currentTarget as HTMLElement));
  $('.tf-date-clear').addEventListener('click', () => { if (id) patchTask(id, { due: null }); });

  const onTasks = () => { if (!destroyed && root.isConnected) paint(); };
  on('tasks', onTasks);
  ensureRenderer().then(() => { if (!destroyed) paint(); });

  return {
    show(next: string) {
      if (id && id !== next) saveBody.flush();
      if (id !== next) { bodySent = '\u0000'; editingTitle = false; }
      id = next;
      root.dataset.task = next;
      paint();
    },
    id: () => id,
    focusTitle: () => editTitle(),
    destroy() {
      saveBody.flush();
      destroyed = true;
      off('tasks', onTasks);
      editor?.destroy();
      editor = null;
      root.innerHTML = '';
    },
  };
}

/** Put a trashed task back (from a {{task:…}} line that shows it as deleted). */
export { restoreTask };
