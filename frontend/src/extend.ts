// The app's extension points: commands (the command palette and their keys), pages (sidebar
// entries that show a page of their own), item-pane sections and item actions (the item menu).
// Core code registers commands here too; plugins (plugins/host.ts) register all four. The views
// read these registries; nothing here touches the DOM, so it is unit-tested in Node.
import type { Item } from './types.js';

/** What ``register`` returns: takes the entry away again. */
export type Dispose = () => void;
/** What a mount function may return: a function that cleans up when the element goes away. */
export type Cleanup = void | (() => void);

export interface Command {
  id: string;
  title: string;
  /** A key like 'Mod-Shift-r' or 'Alt-k' (Mod: ⌘ on a Mac, Ctrl elsewhere). */
  key?: string;
  /** Where it comes from, shown in the palette ('' for Synapse's own). */
  source?: string;
  /** The plugin that added it (its id). */
  owner?: string;
  when?: () => boolean;
  run: () => unknown;
}

export interface Page {
  id: string;
  title: string;
  icon?: string;
  source?: string;
  owner?: string;
  mount: (el: HTMLElement) => Cleanup;
}

export interface ItemSection {
  id: string;
  title: string;
  source?: string;
  owner?: string;
  when?: (item: Item) => boolean;
  mount: (el: HTMLElement, item: Item) => Cleanup;
}

export interface ItemAction {
  id: string;
  title: string;
  icon?: string;
  source?: string;
  owner?: string;
  /** Also for papers from a plugin's list that are not in the library. */
  outside?: boolean;
  when?: (items: Item[]) => boolean;
  run: (items: Item[]) => unknown;
}

type Listener = () => void;

/** Entries by id, in the order they were added; listeners hear about every change. */
export class Registry<T extends { id: string }> {
  private map = new Map<string, T>();
  private listeners: Listener[] = [];

  all(): T[] { return [...this.map.values()]; }
  get(id: string): T | undefined { return this.map.get(id); }

  /** Add (or replace) an entry; the returned function removes this one again. */
  register(entry: T): Dispose {
    if (!entry || typeof entry.id !== 'string' || !entry.id) throw new Error('An entry needs an id');
    this.map.set(entry.id, entry);
    this.changed();
    return () => { if (this.map.get(entry.id) === entry) { this.map.delete(entry.id); this.changed(); } };
  }

  subscribe(fn: Listener): Dispose {
    this.listeners.push(fn);
    return () => { this.listeners = this.listeners.filter((f) => f !== fn); };
  }

  changed(): void {
    for (const fn of [...this.listeners]) {
      try { fn(); } catch (e) { console.error(e); }
    }
  }
}

/** A section of the Settings page a plugin adds (its own controls, beside the settings its
    manifest declares). ``after``: the id of the section it follows (default: before Plugins). */
export interface SettingsSection {
  id: string;
  title: string;
  icon?: string;
  after?: string;
  source?: string;
  owner?: string;
  mount: (el: HTMLElement) => Cleanup;
}

/** A core plugin's existing workspace, listed under Plugins without mounting a second page.
 * Third-party plugins use PluginContext.pages or backend item sources for their sidebar entries. */
export interface NavigationEntry {
  id: string;
  title: string;
  icon: string;
  when?: () => boolean;
  active: () => boolean;
  open: () => unknown;
  badge?: () => { text: string; warning?: string; warningTitle?: string } | null;
}

export const navigation = new Registry<NavigationEntry>();
export const commands = new Registry<Command>();
export const pages = new Registry<Page>();
export const itemSections = new Registry<ItemSection>();
export const itemActions = new Registry<ItemAction>();
export const settingsSections = new Registry<SettingsSection>();
/** Badges on pages' sidebar entries (a count or a short text), by page id. */
export const badges = new Map<string, string>();

/* ------------------------------------------------------------------ keys */
export interface KeySpec { mod: boolean; shift: boolean; alt: boolean; ctrl: boolean; key: string }

/** 'Mod-Shift-r' → its parts, or null when it is not a key this app can take. */
export function parseKey(spec: string): KeySpec | null {
  const s = String(spec || '');
  const parts = s.endsWith('--') ? [...s.slice(0, -2).split('-'), '-'] : s.split('-');
  if (parts.some((p) => !p)) return null;
  const out: KeySpec = { mod: false, shift: false, alt: false, ctrl: false, key: '' };
  for (const p of parts.slice(0, -1)) {
    const m = p.toLowerCase();
    if (m === 'mod' || m === 'cmd' || m === 'meta') out.mod = true;
    else if (m === 'shift') out.shift = true;
    else if (m === 'alt' || m === 'option') out.alt = true;
    else if (m === 'ctrl' || m === 'control') out.ctrl = true;
    else return null;
  }
  const last = parts[parts.length - 1];
  out.key = last.length === 1 ? last.toLowerCase() : last;
  return out.key ? out : null;
}

/** The same spec written one way (to compare keys). */
export function normalKey(spec: string): string | null {
  const k = parseKey(spec);
  if (!k) return null;
  return [k.mod && 'Mod', k.ctrl && 'Ctrl', k.alt && 'Alt', k.shift && 'Shift', k.key].filter(Boolean).join('-');
}

/** Does the key event match ``spec``? ``mac``: whether Mod means ⌘ (else Ctrl). */
export function keyMatches(spec: string, e: Pick<KeyboardEvent, 'key' | 'code' | 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey'>, mac: boolean): boolean {
  const k = parseKey(spec);
  if (!k) return false;
  const modDown = mac ? e.metaKey : e.ctrlKey;
  const ctrlDown = mac ? e.ctrlKey : false;
  if (k.mod !== modDown || k.shift !== e.shiftKey || k.alt !== e.altKey || k.ctrl !== ctrlDown) return false;
  if (!mac && e.metaKey) return false;
  // with Alt (Option on a Mac) the key may type another character: compare the physical key too
  const code = e.code?.startsWith('Key') ? e.code.slice(3).toLowerCase() : e.code?.startsWith('Digit') ? e.code.slice(5) : '';
  const pressed = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  return pressed === k.key || code === k.key;
}

/** How a key is shown: '⌘⇧R' on a Mac, 'Ctrl+Shift+R' elsewhere. */
export function keyLabel(spec: string, mac: boolean): string {
  const k = parseKey(spec);
  if (!k) return '';
  const key = k.key.length === 1 ? k.key.toUpperCase() : k.key;
  if (mac) return `${k.ctrl ? '⌃' : ''}${k.alt ? '⌥' : ''}${k.shift ? '⇧' : ''}${k.mod ? '⌘' : ''}${key}`;
  return [k.mod && 'Ctrl', k.ctrl && 'Ctrl', k.alt && 'Alt', k.shift && 'Shift', key].filter(Boolean).join('+');
}

/** Keys Synapse uses itself (main.ts, the tabs, the editors): a plugin's command cannot take them. */
export const RESERVED_KEYS = new Set([
  'Mod-a', 'Mod-c', 'Mod-v', 'Mod-x', 'Mod-z', 'Mod-Shift-z', 'Mod-y', 'Mod-s', 'Mod-f', 'Mod-g', 'Mod-Shift-f',
  'Mod-n', 'Mod-Shift-n', 'Mod-i', 'Mod-e', 'Mod-t', 'Mod-Shift-t', 'Mod-w', 'Mod-r', 'Mod-l', 'Mod-p', 'Mod-k',
  'Mod-,', 'Mod-Shift-l', 'Mod-Shift-g', 'Mod-Shift-k', 'Mod-Shift-p', 'Mod-Shift-c', 'Mod-\\', 'Mod-Enter',
  'Mod-1', 'Mod-2', 'Mod-3', 'Mod-4', 'Mod-5', 'Mod-6', 'Mod-7', 'Mod-8', 'Mod-9', 'Mod-[', 'Mod-]', 'Mod-b',
].map((k) => normalKey(k)!));

/** Why a plugin's command may not use ``spec`` (or null when it may). Keys need ⌘/Ctrl or Alt,
    so typing is never taken over, and must not be one of Synapse's own. */
export function keyProblem(spec: string): string | null {
  const k = parseKey(spec);
  if (!k) return `"${spec}" is not a key (write it like Mod-Shift-r)`;
  if (!k.mod && !k.alt && !k.ctrl && !/^F\d{1,2}$/.test(k.key)) return `"${spec}" needs Mod or Alt`;
  if (RESERVED_KEYS.has(normalKey(spec)!)) return `"${spec}" is one of Synapse's own keys`;
  return null;
}

/* ------------------------------------------------------------------ the palette's ranking */
/** How well ``query`` matches ``text``: its letters in order (a gap costs), whole words and the
    start count more; -1 when it does not match. */
export function matchScore(query: string, text: string): number {
  const q = query.toLowerCase().trim();
  const t = text.toLowerCase();
  if (!q) return 0;
  if (t.includes(q)) return 1000 - t.indexOf(q) + (t.startsWith(q) ? 200 : 0) + (/\W/.test(t[t.indexOf(q) - 1] ?? ' ') ? 100 : 0);
  let score = 0, ti = 0, last = -1;
  for (const ch of q.replace(/\s+/g, '')) {
    const at = t.indexOf(ch, ti);
    if (at < 0) return -1;
    score += at === last + 1 ? 10 : (at === 0 || /\W/.test(t[at - 1]) ? 6 : 1);
    last = at;
    ti = at + 1;
  }
  return score;
}

/** The commands for the palette, best first: all of them for an empty query (recently used
    ones first), else those that match. */
export function rankCommands(list: Command[], query: string, recent: string[] = []): Command[] {
  const usable = list.filter((c) => { try { return !c.when || c.when(); } catch { return false; } });
  const rec = (c: Command) => { const i = recent.indexOf(c.id); return i < 0 ? 0 : 50 - i; };
  if (!query.trim()) {
    return usable.slice().sort((a, b) => rec(b) - rec(a) || a.title.localeCompare(b.title));
  }
  return usable
    .map((c) => {
      // a match in the title beats one that needs the plugin's name
      const own = matchScore(query, c.title);
      return { c, s: own >= 0 ? own + 2000 : matchScore(query, `${c.source ? `${c.source}: ` : ''}${c.title}`) };
    })
    .filter((x) => x.s >= 0)
    .sort((a, b) => b.s + rec(b.c) - (a.s + rec(a.c)) || a.c.title.localeCompare(b.c.title))
    .map((x) => x.c);
}
