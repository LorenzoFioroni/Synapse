// Plugins' frontends: loads the module of every running plugin that has one, gives it its
// PluginContext (plugins/api.ts) and takes everything it registered away again when it stops.
// Syncs with the server at start, when the change feed says plugins changed, and after Settings
// turned one on or off. A plugin's module is loaded again when the plugin restarts (its start time
// is part of the URL). See docs/plugins.md.
import { api } from '../api.js';
import { type Dispose, badges, commands, itemActions, itemSections, keyProblem, pages } from '../extend.js';
import { icon } from '../icons.js';
import { emit, on, selectedItems } from '../store.js';
import { confirmDialog, promptDialog } from '../ui/dialog.js';
import { toast } from '../ui/toast.js';
import type { Item, PluginInfo } from '../types.js';
import { escapeHtml, formatDate } from '../util.js';
import type { ChangeKind, PluginContext, PluginItem, PluginModule } from './api.js';

interface Loaded {
  info: PluginInfo;
  key: string;
  ctx: PluginContext;
  module: Partial<PluginModule> | null;
  disposers: Dispose[];
  unload: (() => void)[];
  listeners: ((kinds: ChangeKind[]) => void)[];
  style: HTMLLinkElement | null;
}

const loaded = new Map<string, Loaded>();
let syncing: Promise<void> | null = null;
let again = false;
let lastError = 0;

const keyOf = (p: PluginInfo) => `${p.id}@${p.started || ''}`;
const fileUrl = (p: PluginInfo, rel: string) =>
  `/api/plugin-files/${encodeURIComponent(p.id)}/${rel.split('/').map(encodeURIComponent).join('/')}?v=${encodeURIComponent(p.started || '')}`;

/** Report a plugin's error without letting it break the app (one notification every few seconds). */
function failed(p: PluginInfo, what: string, e: unknown): void {
  console.error(`Plugin ${p.id}: ${what}`, e);
  if (Date.now() - lastError < 5000) return;
  lastError = Date.now();
  toast(`${p.name}: ${what} failed (${(e as Error)?.message || e})`, { type: 'error' });
}

/** ``fn`` wrapped so that its errors (also of the promise it returns) are reported, not thrown. */
function guarded<A extends unknown[], R>(p: PluginInfo, what: string, fn: (...args: A) => R): (...args: A) => R | undefined {
  return (...args: A) => {
    try {
      const r = fn(...args);
      if (r instanceof Promise) r.catch((e) => failed(p, what, e));
      return r;
    } catch (e) { failed(p, what, e); return undefined; }
  };
}

const copy = <T>(x: T): T => { try { return structuredClone(x); } catch { return JSON.parse(JSON.stringify(x)); } };

function contextFor(info: PluginInfo, rec: Loaded): PluginContext {
  const pid = info.id;
  const full = (id: string) => `${pid}.${id}`;
  const keep = (d: Dispose): Dispose => { rec.disposers.push(d); return d; };
  const call = <T>(method: 'GET' | 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown, query?: Record<string, unknown>) =>
    api.pluginRoute<T>(pid, method, path, body, query);
  return {
    id: pid,
    name: info.name,
    version: info.version,
    settings: async () => (await api.plugins()).plugins.find((x) => x.id === pid)?.values || {},
    commands: {
      register: (spec) => {
        let key = spec.key;
        const problem = key ? keyProblem(key) : null;
        if (problem) { console.warn(`Plugin ${pid}: command ${spec.id}: ${problem}; it gets no key`); key = undefined; }
        return keep(commands.register({
          id: full(spec.id), title: spec.title, key, source: info.name, owner: pid,
          when: spec.when ? () => !!guarded(info, `command ${spec.id}`, spec.when!)() : undefined,
          run: guarded(info, `command ${spec.id}`, spec.run),
        }));
      },
    },
    pages: {
      register: (spec) => keep(pages.register({
        id: full(spec.id), title: spec.title, icon: spec.icon, source: info.name, owner: pid,
        mount: (el) => guarded(info, `page ${spec.id}`, spec.mount)(el),
      })),
      setBadge: (pageId, value) => {
        const id = full(pageId);
        if (value === null || value === undefined || value === '' || value === 0) badges.delete(id);
        else badges.set(id, String(value).slice(0, 12));
        pages.changed();
      },
      open: (pageId) => emit('open-page', full(pageId)),
    },
    itemPane: {
      register: (spec) => keep(itemSections.register({
        id: full(spec.id), title: spec.title, source: info.name, owner: pid,
        when: spec.when ? (item: Item) => !!guarded(info, `section ${spec.id}`, spec.when!)(copy(item) as PluginItem) : undefined,
        mount: (el, item) => guarded(info, `section ${spec.id}`, spec.mount)(el, copy(item) as PluginItem),
      })),
    },
    itemActions: {
      register: (spec) => {
        const id = full(spec.id);
        const action = {
          id, title: spec.title, icon: spec.icon, source: info.name, owner: pid,
          when: spec.when ? (items: Item[]) => !!guarded(info, `action ${spec.id}`, spec.when!)(copy(items) as PluginItem[]) : undefined,
          run: (items: Item[]) => guarded(info, `action ${spec.id}`, spec.run)(copy(items) as PluginItem[]),
        };
        const offAction = itemActions.register(action);
        // also in the command palette, for the selection
        const offCommand = commands.register({
          id: `${id}#selection`, title: spec.title, source: info.name, owner: pid,
          when: () => { const items = selectedItems(); return items.length > 0 && (!action.when || action.when(items)); },
          run: () => action.run(selectedItems()),
        });
        return keep(() => { offAction(); offCommand(); });
      },
    },
    notify: (message, options = {}) => {
      toast(`${info.name}: ${message}`, {
        type: options.type || 'info',
        action: options.action ? { label: options.action.label, run: guarded(info, 'notification action', options.action.run) } : null,
        timeout: options.timeout ?? 4200,
      });
    },
    backend: {
      get: (path, query) => call('GET', path, undefined, query),
      post: (path, body) => call('POST', path, body ?? {}),
      put: (path, body) => call('PUT', path, body ?? {}),
      delete: (path) => call('DELETE', path),
    },
    library: {
      selected: () => copy(selectedItems()) as PluginItem[],
      item: async (id) => copy(await api.getItem(id)) as PluginItem,
      show: (id) => emit('show-item', id),
    },
    onChange: (fn) => {
      const f = guarded(info, 'change listener', fn);
      rec.listeners.push(f);
      return keep(() => { rec.listeners = rec.listeners.filter((x) => x !== f); });
    },
    onUnload: (fn) => { rec.unload.push(fn); },
    ui: {
      icon: (name, cls = '') => icon(name, cls),
      escapeHtml,
      confirm: (message, o = {}) => confirmDialog(message, o),
      prompt: (label, o = {}) => promptDialog(label, o),
      formatDate: (iso, withTime = false) => formatDate(iso, withTime),
    },
  };
}

async function load(info: PluginInfo): Promise<void> {
  const rec = { info, key: keyOf(info), module: null, disposers: [], unload: [], listeners: [], style: null } as unknown as Loaded;
  rec.ctx = contextFor(info, rec);
  loaded.set(info.id, rec);
  if (info.styles) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = fileUrl(info, info.styles);
    link.dataset.plugin = info.id;
    document.head.appendChild(link);
    rec.style = link;
  }
  if (!info.frontend) return;
  try {
    const mod = (await import(fileUrl(info, info.frontend))) as Partial<PluginModule> & { default?: unknown };
    rec.module = mod;
    const activate = mod.activate || (typeof mod.default === 'function' ? (mod.default as PluginModule['activate']) : null);
    if (!activate) throw new Error('its module exports no activate(p) function');
    if (loaded.get(info.id) !== rec) return;   // stopped meanwhile
    await activate(rec.ctx);
  } catch (e) {
    failed(info, 'starting', e);
  }
}

function unload(id: string): void {
  const rec = loaded.get(id);
  if (!rec) return;
  loaded.delete(id);
  const run = (what: string, fn: () => unknown) => { try { fn(); } catch (e) { console.error(`Plugin ${id}: ${what}`, e); } };
  if (rec.module?.deactivate) run('deactivate', () => rec.module!.deactivate!(rec.ctx));
  for (const fn of rec.unload.reverse()) run('unload', fn);
  for (const d of rec.disposers.reverse()) run('dispose', d);
  for (const k of [...badges.keys()]) if (k.startsWith(`${id}.`)) badges.delete(k);
  pages.changed();
  rec.style?.remove();
}

async function doSync(): Promise<void> {
  let list: PluginInfo[];
  try { list = (await api.plugins()).plugins; } catch { return; }
  const want = new Map(list.filter((p) => p.running && (p.frontend || p.styles)).map((p) => [p.id, p]));
  for (const [id, rec] of [...loaded]) {
    const now = want.get(id);
    if (!now || keyOf(now) !== rec.key) unload(id);
  }
  for (const [id, info] of want) if (!loaded.has(id)) await load(info);
}

/** Bring the loaded plugins in line with the server (one sync at a time; a request during one
    runs another afterwards). */
export function syncPlugins(): Promise<void> {
  if (syncing) { again = true; return syncing; }
  syncing = (async () => {
    do { again = false; await doSync(); } while (again);
  })().finally(() => { syncing = null; });
  return syncing;
}

/** The change feed named these kinds (changes.ts). */
export function pluginChanges(kinds: string[]): void {
  if (kinds.includes('plugins')) syncPlugins();
  for (const rec of loaded.values()) for (const fn of rec.listeners) fn(kinds as ChangeKind[]);
}

export function initPluginHost(): void {
  // the item list reloaded in this window: its own changes are not in the change feed
  let t: ReturnType<typeof setTimeout> | undefined;
  on('items', () => { clearTimeout(t); t = setTimeout(() => { for (const rec of loaded.values()) for (const fn of rec.listeners) fn(['library']); }, 300); });
  syncPlugins();
  window.zcPlugins = { loaded: () => [...loaded.keys()], sync: syncPlugins };   // for tests
}
