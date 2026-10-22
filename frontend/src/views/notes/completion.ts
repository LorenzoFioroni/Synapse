// Autocomplete in the notes editor:
//   [@smi   or   @smi   → citation keys from the library ("@smi" alone becomes "[@key]")
//   [[Rev   or   ![[Rev → note names
//   {{task plot        → tasks ({{task:id}}), or a new task with that title
import type { Completion, CompletionContext, CompletionResult } from '@codemirror/autocomplete';
import type { EditorState } from '@codemirror/state';
import type { EditorView } from '@codemirror/view';
import type { SyntaxNode } from '@lezer/common';
import type { CiteEntry, NoteInfo } from '../../types.js';
import { completionContext, noteChoices, searchItems, searchNotes, shortAuthors } from './library.js';
import { GALLERY_INFO } from './mdscan.js';

type CM = typeof import('../../../vendor/cm6/cm6.js');

const NO_COMPLETE = /^(InlineCode|CodeText|FencedCode|CodeBlock|CodeInfo|CodeMark|Frontmatter|URL|HTMLTag|Comment)$/;
// "{{task" (then ":" or a space, or nothing yet) and the words typed after it, until "}}" closes it
const TASK_START = /\{\{task(?::|\s+|$)([^{}\n]*)$/i;

/** A task autocomplete can offer (from the Tasks module, through taskHooks). */
export interface TaskChoice { id: string; title: string; detail: string; info: string; done: boolean; updated: string }

/** What autocomplete draws on. ``tasks`` and ``newTask`` come from the Tasks module (none: no task completion). */
export interface CompletionData {
  items(): CiteEntry[];
  notes(): NoteInfo[];
  tasks?(): TaskChoice[];
  newTask?(title: string): Promise<string | null>;
  /** A task's title as HTML (math, citations rendered), shown in place of the plain label. */
  taskHtml?(id: string): string;
}

/** Tasks whose title has every word of ``query``: titles starting with it first, open before done, newest first. */
export function searchTasks(tasks: TaskChoice[], query: string, limit = 30): TaskChoice[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const rank = (t: TaskChoice) => {
    const s = t.title.toLowerCase();
    if (!words.length) return 0;
    if (s.startsWith(words.join(' '))) return 0;
    return words.every((w) => new RegExp(`(^|[^\\p{L}\\p{N}])${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`, 'u').test(s)) ? 1 : 2;
  };
  return tasks
    .filter((t) => words.every((w) => t.title.toLowerCase().includes(w)))
    .map((t) => ({ t, r: rank(t) }))
    .sort((a, b) => a.r - b.r || Number(a.t.done) - Number(b.t.done) || b.t.updated.localeCompare(a.t.updated))
    .slice(0, limit)
    .map((x) => x.t);
}

/** The autocompletion extension; ``data`` gives the cite index, the notes overview and the tasks. */
export function completions(cm: CM, data: CompletionData) {
  const taskOf = new WeakMap<Completion, string>();   // the task an option stands for
  // a ```gallery block is a fenced block to CodeMirror, but its captions are text (mdscan.ts)
  const inCode = (state: EditorState, pos: number) => {
    const names: string[] = [];
    let gallery = false;
    for (let n: SyntaxNode | null = cm.syntaxTree(state).resolveInner(pos, -1); n; n = n.parent) {
      names.push(n.name);
      if (n.name === 'FencedCode') { const info = n.getChild('CodeInfo'); gallery = !!info && GALLERY_INFO.test(state.sliceDoc(info.from, info.to).trim()); }
    }
    return names.some((name) => NO_COMPLETE.test(name) && !(gallery && (name === 'CodeText' || name === 'FencedCode')));
  };

  /** "{{task query": the tasks that match, and a new task named "query". Picking one writes its {{task:id}}. */
  function taskSource(ctx: CompletionContext, lineFrom: number, before: string): CompletionResult | null {
    const m = TASK_START.exec(before);
    if (!m || !data.tasks || inCode(ctx.state, ctx.pos)) return null;
    if (((before.slice(0, m.index).match(/(?<!\\)\$/g) || []).length) % 2) return null;   // inside $math$
    const from = lineFrom + m.index;
    const query = m[1].trim();
    if (/^[a-z0-9]{4,32}$/.test(query) && data.tasks().some((t) => t.id === query)) return null;   // inside a whole {{task:id}}
    const close = ctx.state.sliceDoc(ctx.pos, ctx.pos + 2) === '}}' ? 2 : 0;   // closeBrackets typed "}}" already
    const put = (view: EditorView, f: number, t: number, id: string) => {
      const insert = `{{task:${id}}}`;
      view.dispatch({ changes: { from: f, to: t, insert }, selection: { anchor: f + insert.length } });
    };
    const options: Completion[] = searchTasks(data.tasks(), query).map((t) => {
      const o: Completion = {
        label: t.title || '(untitled)',
        detail: t.detail,
        info: t.info || undefined,
        type: t.done ? 'task done' : 'task',
        apply: (view: EditorView, _c: Completion, f: number, to: number) => put(view, f, to + close, t.id),
      };
      taskOf.set(o, t.id);
      return o;
    });
    if (query && data.newTask) {
      options.push({
        label: `New task “${query}”`,
        detail: 'creates it',
        type: 'task new',
        boost: -99,
        apply: (view: EditorView, _c: Completion, f: number, to: number) => {
          const typed = view.state.sliceDoc(f, to + close);
          data.newTask!(query).then((id) => {
            // the task exists now: its line goes where "{{task query" was, if that is still there
            if (id && view.state.sliceDoc(f, f + typed.length) === typed) put(view, f, f + typed.length, id);
          });
        },
      });
    }
    return options.length ? { from, filter: false, options } : null;
  }

  function source(ctx: CompletionContext): CompletionResult | null {
    const line = ctx.state.doc.lineAt(ctx.pos);
    const before = line.text.slice(0, ctx.pos - line.from);
    const tasks = taskSource(ctx, line.from, before);
    if (tasks) return tasks;
    const c = completionContext(before);
    if (!c || inCode(ctx.state, ctx.pos)) return null;
    if (!ctx.explicit && c.kind === 'cite' && !c.bracket && !c.query) return null;   // a lone "@": wait for a letter
    const from = line.from + c.from;

    if (c.kind === 'note') {
      const after = ctx.state.sliceDoc(ctx.pos, ctx.pos + 2);
      return {
        from,
        filter: false,
        options: searchNotes(noteChoices(data.notes()), c.query).map((n): Completion => ({
          label: n.insert,
          detail: n.title !== n.stem ? n.title : n.folder,
          type: 'note',
          apply(view: EditorView, _c: Completion, f: number, t: number) {
            const close = after === ']]' ? '' : ']]';
            const insert = n.insert + close;
            view.dispatch({ changes: { from: f, to: t, insert }, selection: { anchor: f + n.insert.length + 2 } });
          },
        })),
      };
    }

    return {
      from,
      filter: false,
      options: searchItems(data.items(), c.query).map((it): Completion => ({
        label: it.key,
        detail: [shortAuthors(it.authors), it.year].filter(Boolean).join(' '),
        info: it.title || undefined,
        type: 'cite',
        apply(view: EditorView, _c: Completion, f: number, t: number) {
          if (c.bracket) {
            view.dispatch({ changes: { from: f, to: t, insert: it.key }, selection: { anchor: f + it.key.length } });
            return;
          }
          // bare "@query" → "[@key]" (only the bracketed form is a citation)
          const at = f - 1;
          const dash = view.state.sliceDoc(at - 1, at) === '-' ? 1 : 0;
          const insert = `[${dash ? '-' : ''}@${it.key}]`;
          view.dispatch({ changes: { from: at - dash, to: t, insert }, selection: { anchor: at - dash + insert.length } });
        },
      })),
    };
  }

  return cm.autocompletion({
    override: [source], icons: false, activateOnTyping: true, maxRenderedOptions: 30,
    // a task's title is drawn rendered (its math, citations) in place of the plain label
    optionClass: (c) => (taskOf.has(c) && data.taskHtml ? 'cm-task-option' : ''),
    addToOptions: [{
      position: 45,
      render: (c) => {
        const id = taskOf.get(c);
        if (!id || !data.taskHtml) return null;
        const el = document.createElement('span');
        el.className = 'cm-task-label';
        el.innerHTML = data.taskHtml(id);
        return el;
      },
    }],
  });
}
