// The phone shell's decisions without a DOM (unit-tested in tests/js/phone.test.mjs): which
// plugin lists and pages it offers, what the tab bar holds, the Plugins tab's badge, a list's
// groups, a row's second line and what a swipe means.
import type { Item, PluginSource } from '../types.js';

/** A plugin's list of papers or page, as the phone offers it. */
export interface Destination {
  /** "source:<plugin>.<id>" or "page:<plugin>.<id>". */
  id: string;
  kind: 'source' | 'page';
  title: string;
  icon: string;
  badge: string | null;
  plugin: string;
  pluginName: string;
  /** Usable on a phone (a list always is; a page when it draws a phone layout). */
  phone: boolean;
}

export interface PageLike { id: string; title: string; icon?: string; owner?: string; source?: string; phone?: unknown }

/** The lists and pages of the running plugins (lists first, as the desktop sidebar has them). */
export function destinations(sources: PluginSource[], pages: PageLike[], badges: Map<string, string>): Destination[] {
  return [
    ...sources.map((s): Destination => ({
      id: `source:${s.id}`, kind: 'source', title: s.title, icon: s.icon || 'list', badge: s.badge || null,
      plugin: s.plugin, pluginName: s.plugin_name || s.plugin, phone: true,
    })),
    ...pages.map((p): Destination => ({
      id: `page:${p.id}`, kind: 'page', title: p.title, icon: p.icon || 'plugin', badge: badges.get(p.id) || null,
      plugin: p.owner || '', pluginName: p.source || p.owner || '', phone: !!p.phone,
    })),
  ];
}

/** The destination in the tab bar next to Notes: the one chosen (if still there), else the first
 *  usable one; ``saved`` 'none' means the reader wants none. */
export function pinned(dests: Destination[], saved: string | null): Destination | null {
  if (saved === 'none') return null;
  const usable = dests.filter((d) => d.phone);
  return usable.find((d) => d.id === saved) || usable[0] || null;
}

export type TabId = 'library' | 'notes' | 'add' | 'pin' | 'plugins';

/** The tab bar, left to right: Library, Notes, the "+" button, the pinned destination and Plugins.
 *  Without any plugin list or page usable here there is no Plugins tab, and the "+" sits between
 *  Library and Notes. */
export function tabSlots(dests: Destination[], pin: Destination | null, { notes = true } = {}): TabId[] {
  const plugins = dests.some((d) => d.phone);
  if (!plugins) return notes ? ['library', 'add', 'notes'] : ['library', 'add'];
  return [
    'library', ...(notes ? ['notes' as const] : []), 'add',
    ...(pin ? ['pin' as const] : []), 'plugins',
  ];
}

/** The Plugins tab's badge: the counts of the usable destinations not pinned, added up (a dot
 *  when some only have text). */
export function hubBadge(dests: Destination[], pin: Destination | null): string | null {
  let sum = 0, text = false;
  for (const d of dests) {
    if (!d.phone || d === pin || d.id === pin?.id || !d.badge) continue;
    const n = Number(d.badge);
    if (Number.isFinite(n)) sum += n; else text = true;
  }
  return sum > 0 ? String(sum) : text ? '•' : null;
}

/** Runs of consecutive items with the same ``_group`` (a plugin's tiers, say), in list order. */
export function groupRuns<T extends Pick<Item, '_group'>>(items: T[]): { group: string | null; items: T[] }[] {
  const out: { group: string | null; items: T[] }[] = [];
  for (const it of items) {
    const g = it._group || null;
    const last = out[out.length - 1];
    if (last && last.group === g) last.items.push(it);
    else out.push({ group: g, items: [it] });
  }
  return out;
}

/** Family names, as the list shows them: one, two, or the first "et al.". */
export function familyNames(authors: string | null | undefined): string {
  const fams = String(authors || '').split(';').map((a) => a.split(',')[0].trim()).filter(Boolean);
  if (fams.length < 3) return fams.join(' and ');
  return `${fams[0]} et al.`;
}

/** A row's second line: authors · year · where it was published. */
export function itemMeta(it: Pick<Item, 'authors' | 'year' | 'journal' | 'arxiv' | 'item_type'>): string {
  const where = it.journal || (it.arxiv ? 'arXiv' : '');
  return [familyNames(it.authors), it.year ? String(it.year) : '', where].filter(Boolean).join(' · ');
}

/** What letting go of a row moved by ``dx`` pixels (of ``width``) means: past about a third of the
 *  row, right adds and left dismisses. */
export function swipeAction(dx: number, width: number): 'add' | 'dismiss' | null {
  const need = Math.max(80, width * 0.3);
  if (dx >= need) return 'add';
  if (dx <= -need) return 'dismiss';
  return null;
}

/** "now", "5 min", "3 h", a weekday within the week, else "4 Nov" (and the year when not this one). */
export function shortWhen(seconds: number, now: Date = new Date()): string {
  const d = new Date(seconds * 1000);
  const ago = (now.getTime() - d.getTime()) / 1000;
  if (ago < 60) return 'now';
  if (ago < 3600) return `${Math.floor(ago / 60)} min`;
  if (ago < 86400 && d.getDate() === now.getDate()) return `${Math.floor(ago / 3600)} h`;
  if (ago < 6 * 86400) return d.toLocaleDateString('en-GB', { weekday: 'short' });
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...(d.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}) });
}
