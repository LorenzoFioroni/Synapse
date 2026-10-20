// The app's own tabs, without a DOM: the Synapse tab (the library, Notes and Tasks) always comes
// first and cannot be closed; after it come the PDF tabs, one per PDF unless the reader asked for
// another. tabs.ts draws them and holds a viewer page per tab.

/** A PDF tab. `key` names the PDF (api.ts `fileUrl`): opening the same PDF again reuses its tab. */
export interface PdfTab {
  id: string;
  key: string;
  /** The viewer's address (`/viewer.html?…`), kept up to date with the page being read. */
  url: string;
  title: string;
  /** The tab that was active when this one was opened, until either is left for another: closing
   *  this one goes back there, as in a browser. */
  opener: string | null;
  /** When it was last shown (a counter): of two tabs of one PDF, the later one is reused. */
  used: number;
}

/** What is kept between visits (localStorage). */
export interface SavedTabs {
  v: 1;
  tabs: { key: string; url: string; title: string }[];
  /** Index into `tabs`, or -1 for the Synapse tab. */
  active: number;
}

export const HOME = 'home';
const MAX_CLOSED = 10;
const VIEWER = '/viewer.html?';

export class TabSet {
  tabs: PdfTab[] = [];
  active: string = HOME;
  /** Recently closed tabs, the last one closed at the end. */
  closed: PdfTab[] = [];
  private tick = 0;
  private nextId = 1;

  get(id: string): PdfTab | null {
    return this.tabs.find((t) => t.id === id) || null;
  }

  /** The tab showing this PDF: the one shown last when there are several. */
  byKey(key: string): PdfTab | null {
    let best: PdfTab | null = null;
    for (const t of this.tabs) if (t.key === key && (!best || t.used > best.used)) best = t;
    return best;
  }

  /** A new tab at the end, shown unless `activate` is false. */
  add(o: { key: string; url: string; title: string }, { activate = true } = {}): PdfTab {
    const tab: PdfTab = { id: `t${this.nextId++}`, key: o.key, url: o.url, title: o.title || 'PDF', opener: activate ? this.active : null, used: 0 };
    this.tabs.push(tab);
    if (activate) this.activate(tab.id);
    return tab;
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
    this.closed.push({ ...tab, opener: null });
    if (this.closed.length > MAX_CLOSED) this.closed.shift();
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

  /** The last closed tab, open again and shown. */
  reopen(): PdfTab | null {
    const t = this.closed.pop();
    return t ? this.add(t) : null;
  }

  /** Move a PDF tab to position `index` among the PDF tabs. */
  move(id: string, index: number): void {
    const i = this.tabs.findIndex((t) => t.id === id);
    if (i < 0) return;
    const [t] = this.tabs.splice(i, 1);
    this.tabs.splice(Math.max(0, Math.min(index, this.tabs.length)), 0, t);
  }

  /** The tab `dir` steps away from the active one, going round (the Synapse tab included). */
  step(dir: number): string {
    const ids = [HOME, ...this.tabs.map((t) => t.id)];
    const i = ids.indexOf(this.active);
    return ids[(((i + dir) % ids.length) + ids.length) % ids.length];
  }

  /** The n-th tab as browsers count them: 1 is the Synapse tab, 9 is always the last tab. */
  nth(n: number): string | null {
    const ids = [HOME, ...this.tabs.map((t) => t.id)];
    if (n === 9) return ids[ids.length - 1];
    return ids[n - 1] ?? null;
  }

  /** What to keep between visits; `urlOf` gives a tab's address as it is now. */
  save(urlOf: (t: PdfTab) => string = (t) => t.url): SavedTabs {
    return {
      v: 1,
      tabs: this.tabs.map((t) => ({ key: t.key, url: urlOf(t), title: t.title })),
      active: this.tabs.findIndex((t) => t.id === this.active),
    };
  }

  /** The tabs kept by `save`, ignoring anything that does not look like them. */
  static restore(saved: unknown): TabSet {
    const set = new TabSet();
    const s = saved as Partial<SavedTabs> | null;
    if (!s || s.v !== 1 || !Array.isArray(s.tabs)) return set;
    const ids: (string | null)[] = [];
    for (const t of s.tabs) {
      const ok = t && typeof t.key === 'string' && typeof t.url === 'string' && t.url.startsWith(VIEWER) && typeof t.title === 'string';
      ids.push(ok ? set.add(t, { activate: false }).id : null);
    }
    const active = typeof s.active === 'number' ? ids[s.active] : null;
    if (active) set.activate(active);
    return set;
  }
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
