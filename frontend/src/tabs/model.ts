// The app's own tabs, without a DOM. The first tab (Library or Tasks) always comes first and cannot
// be closed. After it come note tabs and PDF tabs, in the order they were opened. tabs.ts draws
// them, holds a viewer page per PDF tab and has the notes workspace show a note tab's note.
//
// Notes open as in VS Code: in a *preview* tab (its title in italics) that the next note opened
// replaces, until it is kept (double-click on the tab, editing the note, double-click in the notes
// list). There is at most one preview tab. A note tab with no note (key '') is Notes with nothing
// open; the next note opened fills it. PDFs get a kept tab each, one per PDF unless the reader asks
// for another.

export type TabKind = 'pdf' | 'note';

/** A tab after the first one. `key` names what it shows: the PDF (api.ts `fileUrl`) or the note's
 *  path ('' for none). Opening the same PDF or note again shows its tab. */
export interface Tab {
  id: string;
  kind: TabKind;
  key: string;
  /** A PDF tab's viewer address (`/viewer.html?…`), kept up to date with the page being read;
   *  '' for a note tab. */
  url: string;
  title: string;
  /** A note tab the next note opened replaces (see the top of this file). */
  preview: boolean;
  /** A note tab's mode (live, source, read); undefined: the mode last chosen anywhere. */
  mode?: string;
  /** The tab that was active when this one was opened, until either is left for another: closing
   *  this one goes back there, as in a browser. */
  opener: string | null;
  /** When it was last shown (a counter): of two tabs of one PDF, the later one is reused. */
  used: number;
}

/** What a tab is made from (and what is kept of it). */
export interface TabSpec { kind?: TabKind; key: string; url?: string; title?: string; preview?: boolean; mode?: string }

/** What a window keeps for a reload (its session storage). */
export interface SavedTabs {
  v: 1;
  tabs: { kind: TabKind; key: string; url: string; title: string; preview: boolean; mode?: string }[];
  /** Index into `tabs`, or -1 for the first tab. */
  active: number;
}

export const HOME = 'home';
const MAX_CLOSED = 10;
const VIEWER = '/viewer.html?';
const MODES = new Set(['live', 'source', 'read']);

/** A note tab's title: the note's name, or "Notes" with none. */
export const noteTitle = (path: string): string => (path ? path.split('/').pop()!.replace(/\.md$/i, '') : 'Notes');

export class TabSet {
  tabs: Tab[] = [];
  active: string = HOME;
  /** Recently closed tabs, the last one closed at the end. */
  closed: Tab[] = [];
  private tick = 0;
  private nextId = 1;

  get(id: string): Tab | null {
    return this.tabs.find((t) => t.id === id) || null;
  }

  /** The tab showing this PDF or note: the one shown last when there are several. */
  byKey(key: string, kind: TabKind = 'pdf'): Tab | null {
    let best: Tab | null = null;
    for (const t of this.tabs) if (t.kind === kind && t.key === key && (!best || t.used > best.used)) best = t;
    return best;
  }

  previewTab(): Tab | null {
    return this.tabs.find((t) => t.preview) || null;
  }

  /** The note tab shown last. */
  lastNote(): Tab | null {
    let best: Tab | null = null;
    for (const t of this.tabs) if (t.kind === 'note' && (!best || t.used > best.used)) best = t;
    return best;
  }

  /** A new tab, at the end or at `at` (an index among the tabs after the first), shown unless
   *  `activate` is false. */
  add(o: TabSpec, { activate = true, at }: { activate?: boolean; at?: number } = {}): Tab {
    const kind = o.kind || 'pdf';
    const tab: Tab = {
      id: `t${this.nextId++}`, kind, key: o.key, url: kind === 'pdf' ? o.url || '' : '',
      title: o.title || (kind === 'note' ? noteTitle(o.key) : 'PDF'), preview: kind === 'note' && !!o.preview,
      opener: activate ? this.active : null, used: 0,
    };
    if (kind === 'note' && typeof o.mode === 'string' && MODES.has(o.mode)) tab.mode = o.mode;
    if (tab.preview) { const p = this.previewTab(); if (p) p.preview = false; }   // only one
    const i = at === undefined ? this.tabs.length : Math.max(0, Math.min(at, this.tabs.length));
    this.tabs.splice(i, 0, tab);
    if (activate) this.activate(tab.id);
    return tab;
  }

  /** Where a new tab opened from the active one goes: right after it. */
  afterActive(): number {
    const i = this.tabs.findIndex((t) => t.id === this.active);
    return i < 0 ? this.tabs.length : i + 1;
  }

  /**
   * Show a note (`path`; null: Notes with nothing open) in a tab, VS Code's way: its own tab when it
   * has one, else the preview tab, which it takes over, else a new tab after the active one.
   * `keep`: the tab it ends up in is kept. `last` (with no path): the note tab shown last, if any.
   */
  openNote(path: string | null, { keep = false, last = false } = {}): Tab {
    if (path === null && last) {
      const t = this.lastNote();
      if (t) { if (keep) t.preview = false; this.activate(t.id); return t; }
    }
    const key = path ?? '';
    const own = this.byKey(key, 'note');
    if (own) { if (keep) own.preview = false; this.activate(own.id); return own; }
    const p = this.previewTab();
    if (p && (!keep || !p.key)) {   // an empty Notes tab is filled even with a kept note
      p.key = key;
      p.title = noteTitle(key);
      if (keep) p.preview = false;
      this.activate(p.id);
      return p;
    }
    return this.add({ kind: 'note', key, preview: !keep }, { at: this.afterActive() });
  }

  /** The tab is kept: the next note opened no longer replaces it. */
  keep(id: string): boolean {
    const t = this.get(id);
    if (!t?.preview) return false;
    t.preview = false;
    return true;
  }

  activate(id: string): void {
    if (id !== HOME && !this.get(id)) return;
    if (id !== this.active) {
      const left = this.get(this.active);
      if (left) left.opener = null;   // left for another tab: closing it no longer goes back
    }
    this.active = id;
    const t = this.get(id);
    if (t) t.used = ++this.tick;
  }

  /** Close a tab; when it was the active one, show its opener, else the tab to its right, else the
   *  one to its left. Returns whether it was there. */
  close(id: string): boolean {
    const i = this.tabs.findIndex((t) => t.id === id);
    if (i < 0) return false;
    const [tab] = this.tabs.splice(i, 1);
    if (tab.kind === 'pdf' || tab.key) {   // an empty Notes tab is not worth reopening
      this.closed.push({ ...tab, opener: null });
      if (this.closed.length > MAX_CLOSED) this.closed.shift();
    }
    for (const t of this.tabs) if (t.opener === id) t.opener = null;
    if (this.active === id) {
      const back = tab.opener && (tab.opener === HOME || this.get(tab.opener)) ? tab.opener : null;
      this.active = HOME;   // so that activate() below does not clear an opener for the closed tab
      this.activate(back || this.tabs[i]?.id || this.tabs[i - 1]?.id || HOME);
    }
    return true;
  }

  closeOthers(id: string): void {
    for (const t of [...this.tabs]) if (t.id !== id) this.close(t.id);
  }

  closeRight(id: string): void {
    const i = this.tabs.findIndex((t) => t.id === id);
    if (i < 0) return;
    for (const t of this.tabs.slice(i + 1).reverse()) this.close(t.id);
  }

  closeAll(): void {
    for (const t of [...this.tabs].reverse()) this.close(t.id);
  }

  /** The last closed tab, open again (kept) and shown. A note already open again just shows. */
  reopen(): Tab | null {
    const t = this.closed.pop();
    if (!t) return null;
    if (t.kind === 'note') return this.openNote(t.key, { keep: true });
    return this.add({ ...t, preview: false });
  }

  /** Notes were moved or renamed: their tabs follow (`rename` maps an old path to its new one). */
  renameNotes(rename: (path: string) => string): void {
    for (const t of [...this.tabs, ...this.closed]) {
      if (t.kind !== 'note' || !t.key) continue;
      const to = rename(t.key);
      if (to !== t.key) { t.key = to; t.title = noteTitle(to); }
    }
  }

  /** Notes were deleted: their tabs close, except the active one, which becomes Notes with nothing
   *  open (the notes workspace stays on screen). Returns the ids of the tabs closed. */
  notesGone(gone: (path: string) => boolean): string[] {
    const closed: string[] = [];
    for (const t of [...this.tabs]) {
      if (t.kind !== 'note' || !t.key || !gone(t.key)) continue;
      if (t.id === this.active) {
        const p = this.previewTab();
        if (p && p.id !== t.id) { this.tabs.splice(this.tabs.indexOf(p), 1); closed.push(p.id); }
        t.key = '';
        t.title = noteTitle('');
        t.preview = true;
      } else {
        this.tabs.splice(this.tabs.indexOf(t), 1);
        closed.push(t.id);
      }
    }
    this.closed = this.closed.filter((t) => t.kind !== 'note' || !gone(t.key));
    return closed;
  }

  /** Move a tab to position `index` among the tabs after the first. */
  move(id: string, index: number): void {
    const i = this.tabs.findIndex((t) => t.id === id);
    if (i < 0) return;
    const [t] = this.tabs.splice(i, 1);
    this.tabs.splice(Math.max(0, Math.min(index, this.tabs.length)), 0, t);
  }

  /** The tab `dir` steps away from the active one, going round (the first tab included). */
  step(dir: number): string {
    const ids = [HOME, ...this.tabs.map((t) => t.id)];
    const i = ids.indexOf(this.active);
    return ids[(((i + dir) % ids.length) + ids.length) % ids.length];
  }

  /** The n-th tab as browsers count them: 1 is the first tab, 9 is always the last tab. */
  nth(n: number): string | null {
    const ids = [HOME, ...this.tabs.map((t) => t.id)];
    if (n === 9) return ids[ids.length - 1];
    return ids[n - 1] ?? null;
  }

  /** What to keep for a reload; `urlOf` gives a PDF tab's address as it is now. */
  save(urlOf: (t: Tab) => string = (t) => t.url): SavedTabs {
    return {
      v: 1,
      tabs: this.tabs.map((t) => ({ kind: t.kind, key: t.key, url: t.kind === 'pdf' ? urlOf(t) : '', title: t.title, preview: t.preview, ...(t.mode ? { mode: t.mode } : {}) })),
      active: this.tabs.findIndex((t) => t.id === this.active),
    };
  }

  /** The tabs kept by `save`, ignoring anything that does not look like them. */
  static restore(saved: unknown): TabSet {
    const set = new TabSet();
    const s = saved as Partial<SavedTabs> | null;
    if (!s || s.v !== 1 || !Array.isArray(s.tabs)) return set;
    const ids: (string | null)[] = [];
    for (const t of s.tabs) ids.push(validSpec(t) ? set.add(t, { activate: false }).id : null);
    const active = typeof s.active === 'number' ? ids[s.active] : null;
    if (active) set.activate(active);
    return set;
  }
}

/** Whether `t` is a tab as `save` writes it (or as another window sends it when a tab is dragged
 *  across): a PDF tab with a viewer address, or a note tab with a path. */
export function validSpec(t: unknown): t is TabSpec {
  const o = t as Partial<TabSpec> | null;
  if (!o || typeof o.key !== 'string' || (o.title !== undefined && typeof o.title !== 'string')) return false;
  const kind = o.kind ?? 'pdf';
  if (kind === 'pdf') return typeof o.url === 'string' && o.url.startsWith(VIEWER);
  return kind === 'note' && (o.key === '' || /\.md$/i.test(o.key));
}

/** A viewer address for reopening a tab: at `page`, without the spot it was opened at (which
 *  would flash again). */
export function reopenUrl(url: string, page: number | null | undefined): string {
  const [path, query = ''] = url.split('?');
  const p = new URLSearchParams(query);
  p.delete('q');
  p.delete('rect');
  if (page && page > 1) p.set('page', String(page));
  else p.delete('page');
  return `${path}?${p}`;
}

/** The viewer address of a tab pointed at another spot of its PDF (for a tab not loaded yet). */
export function urlAtSpot(url: string, spot: { page?: number | null; q?: string | null; rect?: string | null }): string {
  const [path, query = ''] = url.split('?');
  const p = new URLSearchParams(query);
  for (const k of ['page', 'q', 'rect'] as const) {
    const v = spot[k];
    if (v) p.set(k, String(v)); else p.delete(k);
  }
  return `${path}?${p}`;
}
