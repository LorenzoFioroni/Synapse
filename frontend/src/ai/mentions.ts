// Citing papers and notes in a question: "[@" lists library items and "[[" lists notes, with the
// notes editor's matching, labels and insert text (views/notes/library.ts), as a small keyboard-driven
// list over the question box (the box is a plain textarea, not CodeMirror). The data is what the
// editor's autocomplete draws on: GET /api/notes/cite_index and GET /api/notes. Plain fetch, loaded on
// first use, so the PDF viewer (which has neither the app's modules nor its stores) works the same.
// Strings from the library are only ever set as text.
import type { CiteEntry, NoteInfo, NotePdf, NotesListing } from '../types.js';
import { completionContext, noteChoices, pdfChoices, searchItems, searchNotes, shortAuthors } from '../views/notes/library.js';

/** The library data a question can cite; each part is fetched once, when first asked for. */
export class MentionData {
  private cites: CiteEntry[] | null = null;
  private noteList: NoteInfo[] | null = null;
  private pdfList: NotePdf[] = [];   // the PDFs of the notes folders, from the same listing
  private jobs: { cite?: Promise<void>; note?: Promise<void> } = {};
  private failed = { cite: false, note: false };
  /** Called when a part has arrived (the thread draws its citations and links again). */
  onLoad: () => void = () => {};

  items = (): CiteEntry[] => this.cites || [];
  itemsLoaded = (): boolean => !!this.cites;
  notes = (): NoteInfo[] => this.noteList || [];
  pdfs = (): NotePdf[] => this.pdfList;
  notesLoaded = (): boolean => !!this.noteList;
  /** The last attempt to fetch this part failed (the next use tries again). */
  loadFailed = (kind: 'cite' | 'note'): boolean => this.failed[kind];

  load(kind: 'cite' | 'note'): Promise<void> {
    const get = async (url: string) => { const r = await fetch(url); if (!r.ok) throw new Error(String(r.status)); return r.json(); };
    this.jobs[kind] ??= (kind === 'cite'
      ? get('/api/notes/cite_index').then((d: { items: CiteEntry[] }) => { this.cites = d.items; })
      : get('/api/notes').then((d: NotesListing) => { this.noteList = d.notes; this.pdfList = d.pdfs || []; })
    ).then(() => { this.failed[kind] = false; this.onLoad(); }, () => { this.failed[kind] = true; this.jobs[kind] = undefined; });   // a failure may be retried by the next use
    return this.jobs[kind]!;
  }

  /** The path a [[target]] names (the backend resolves by file name, or a path without ".md"); undefined until the notes are known. */
  resolveNote = (target: string): string | null | undefined => {
    if (!this.noteList) { void this.load('note'); return undefined; }
    if (/\.pdf$/i.test(target.trim())) return this.findPdf(target)?.path ?? null;   // [[folder/file.pdf]]
    const t = target.trim().toLowerCase().replace(/\.md$/i, '');
    if (!t) return null;
    const stem = (p: string) => p.replace(/\.md$/i, '').toLowerCase();
    const hits = this.noteList.filter((n) => stem(n.path) === t || stem(n.path).split('/').pop() === t);
    hits.sort((a, b) => a.path.split('/').length - b.path.split('/').length || a.path.localeCompare(b.path));
    return hits[0]?.path ?? null;
  };

  /** The PDF of the notes folders a link target or path names: its path, else a folder path ending it, else its file name. */
  findPdf = (target: string): NotePdf | undefined => {
    const t = target.trim().replace(/\\/g, '/').toLowerCase();
    const hits = this.pdfList.filter((p) => p.path.toLowerCase() === t || p.path.toLowerCase().endsWith(`/${t}`));
    hits.sort((a, b) => a.path.split('/').length - b.path.split('/').length || a.path.localeCompare(b.path));
    return hits[0];
  };
}

interface Row { label: string; detail: string; title: string; insert: string; kind: 'cite' | 'note' }
interface Trigger { kind: 'cite' | 'note'; query: string; from: number; to: number }

/** What the caret is typing, if it is the query of a "[@" or "[[" (bare "@" is not offered: addresses and handles). */
export function mentionTrigger(text: string, caret: number): Trigger | null {
  const lineStart = text.lastIndexOf('\n', caret - 1) + 1;
  const c = completionContext(text.slice(lineStart, caret));
  if (!c || (c.kind === 'cite' && !c.bracket)) return null;
  return { kind: c.kind, query: c.query, from: lineStart + c.from, to: caret };
}

/** The text and caret after choosing a row: "[@key]" / "[[note]]" completed, the caret after the closing bracket. */
export function insertMention(text: string, t: Trigger, insert: string): { text: string; caret: number } {
  const close = t.kind === 'note' ? ']]' : ']';
  const after = text.slice(t.to);
  const skip = after.startsWith(close) ? close.length : 0;
  const put = insert + close;
  return { text: text.slice(0, t.from) + put + after.slice(skip), caret: t.from + put.length };
}

export interface Mentions {
  /** Is the list open? */
  isOpen(): boolean;
  /** Close it; true if it was open. */
  close(): boolean;
  /** Put "[@" at the caret and open the list. */
  startCite(): void;
}

/** Attach the list to ``input``; it opens above ``anchor`` (the positioned question box around the textarea). */
export function attachMentions(input: HTMLTextAreaElement, anchor: HTMLElement, data: MentionData): Mentions {
  const list = document.createElement('div');
  list.className = 'ai-mentions';
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', 'Papers, notes and PDFs');
  list.id = `ai-mentions-${Math.random().toString(36).slice(2, 8)}`;
  list.hidden = true;
  anchor.append(list);
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-controls', list.id);

  let trigger: Trigger | null = null;
  let rows: Row[] = [];
  let active = 0;

  const open = () => !list.hidden;
  function close(): boolean {
    const was = open();
    list.hidden = true;
    trigger = null;
    input.removeAttribute('aria-activedescendant');
    return was;
  }

  function search(t: Trigger): Row[] {
    if (t.kind === 'note') {
      const notes = searchNotes(noteChoices(data.notes()), t.query).map((n) => ({
        label: n.insert, detail: n.title !== n.stem ? n.title : n.folder, title: '', insert: n.insert, kind: 'note' as const,
      }));
      const pdfs = searchNotes(pdfChoices(data.pdfs()), t.query).map((n) => ({
        label: n.insert, detail: n.folder ? `PDF · ${n.folder}` : 'PDF', title: '', insert: n.insert, kind: 'note' as const,
      }));
      return [...notes, ...pdfs].slice(0, 30);
    }
    return searchItems(data.items(), t.query).map((it) => ({
      label: it.key, detail: [shortAuthors(it.authors), it.year].filter(Boolean).join(' '), title: it.title || '', insert: it.key, kind: 'cite' as const,
    }));
  }

  function mark(i: number): void {
    active = i;
    [...list.children].forEach((c, n) => {
      c.classList.toggle('focused', n === i);
      c.setAttribute('aria-selected', String(n === i));
    });
    const row = list.children[i] as HTMLElement | undefined;
    if (row?.id) input.setAttribute('aria-activedescendant', row.id);
    row?.scrollIntoView({ block: 'nearest' });
  }

  function draw(): void {
    if (!trigger) return;
    rows = search(trigger);
    list.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'ai-mention-empty';
      const loading = trigger.kind === 'note' ? !data.notesLoaded() : !data.itemsLoaded();
      const failed = loading && data.loadFailed(trigger.kind);
      empty.textContent = failed ? `Could not load ${trigger.kind === 'note' ? 'notes' : 'papers'}` : loading ? 'Loading…' : trigger.kind === 'note' ? 'No matching note or PDF' : 'No matching paper';
      list.append(empty);
      input.removeAttribute('aria-activedescendant');
    }
    rows.forEach((r, n) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'menu-item ai-mention';
      b.id = `${list.id}-${n}`;
      b.setAttribute('role', 'option');
      const top = document.createElement('span');
      top.className = 'ai-mention-top';
      const key = document.createElement('span');
      key.className = 'ai-mention-key';
      key.textContent = r.label;
      const detail = document.createElement('span');
      detail.className = 'ai-mention-detail';
      detail.textContent = r.detail;
      top.append(key, detail);
      b.append(top);
      if (r.title) {
        const title = document.createElement('span');
        title.className = 'ai-mention-title';
        title.textContent = r.title;
        b.append(title);
      }
      b.addEventListener('mousemove', () => { if (active !== n) mark(n); });
      b.addEventListener('click', () => choose(n));
      list.append(b);
    });
    list.hidden = false;
    if (rows.length) mark(Math.min(active, rows.length - 1));
  }

  function choose(n: number): void {
    const r = rows[n], t = trigger;
    if (!r || !t) return;
    const next = insertMention(input.value, t, r.insert);
    close();
    input.value = next.text;
    input.setSelectionRange(next.caret, next.caret);
    input.focus();
    input.dispatchEvent(new Event('input', { bubbles: true }));   // not a new trigger: the caret sits after the brackets
  }

  /** Look at the text before the caret: open, update or close the list. */
  function refresh(): void {
    const t = input.selectionStart === input.selectionEnd ? mentionTrigger(input.value, input.selectionStart) : null;
    if (!t) { close(); return; }
    if (!trigger || trigger.kind !== t.kind || trigger.from !== t.from) active = 0;
    trigger = t;
    draw();
    if (t.kind === 'note' ? !data.notesLoaded() : !data.itemsLoaded()) {
      const kind = t.kind;
      void data.load(kind).then(() => { if (trigger?.kind === kind) draw(); });   // draws "Could not load" after a failure
    }
  }

  input.addEventListener('input', refresh);
  input.addEventListener('click', refresh);
  input.addEventListener('keyup', (e) => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) refresh(); });
  input.addEventListener('blur', () => { close(); });
  // the list keeps the caret in the textarea: a press on it must not blur it
  list.addEventListener('pointerdown', (e) => e.preventDefault());
  input.addEventListener('keydown', (e) => {
    if (!open() || e.isComposing) return;
    const n = rows.length;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!n) return;
      e.preventDefault();
      mark((active + (e.key === 'ArrowDown' ? 1 : n - 1)) % n);
    } else if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
      if (!n) { close(); return; }
      e.preventDefault();
      e.stopImmediatePropagation();   // not a send
      choose(active);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
    }
  }, true);

  return {
    isOpen: open,
    close,
    startCite() {
      const at = input.selectionStart ?? input.value.length;
      const before = input.value.slice(0, at);
      const lead = before && !/\s$/.test(before) ? ' ' : '';
      input.setRangeText(`${lead}[@`, at, input.selectionEnd ?? at, 'end');
      input.focus();
      refresh();
    },
  };
}
