// Plugins' writes made while Synapse could not be reached (plugin API 1.16, docs/plugins.md#offline-use).
// A plugin whose manifest says ``"offline": {"queue": true}`` passes ``{ offline: () => answer }`` to
// a write: when Synapse cannot be reached, the write is kept here (IndexedDB) and the plugin gets
// that answer at once. Once Synapse answers again they are sent in order, with the time they were
// made (``X-Synapse-Offline``); the plugin's route merges them. A 409 with ``conflict`` asks the
// user: Keep mine sends ``conflict.retry`` again with ``X-Synapse-Offline-Force``. Another refusal
// drops the write and says so; an error or no answer leaves the rest for later.
import { type ApiError, api } from '../api.js';
import { offlineEnabled } from '../offline.js';
import { openDialog } from '../ui/dialog.js';
import { toast } from '../ui/toast.js';
import { escapeHtml } from '../util.js';

const DB = 'synapse-offline-plugins';
const STORE = 'queue';
export type WriteMethod = 'POST' | 'PATCH' | 'PUT' | 'DELETE';
export interface QueuedWrite { seq?: number; plugin: string; method: WriteMethod; path: string; body: unknown; at: number }

function idb(): Promise<IDBDatabase> {
  return new Promise((ok, no) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: 'seq', autoIncrement: true });
    r.onsuccess = () => ok(r.result);
    r.onerror = () => no(r.error);
  });
}
async function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T> | void): Promise<T | undefined> {
  const db = await idb();
  return new Promise((ok, no) => {
    const t = db.transaction(STORE, mode);
    const req = fn(t.objectStore(STORE));
    t.oncomplete = () => { db.close(); ok(req ? req.result : undefined); };
    t.onerror = () => { db.close(); no(t.error); };
  });
}

let mem: QueuedWrite[] = [];
let loaded: Promise<void> | null = null;
const synced = new Map<string, Set<() => void>>();

export function loadQueue(): Promise<void> {
  if (!offlineEnabled()) return Promise.resolve();
  loaded ||= tx<QueuedWrite[]>('readonly', (s) => s.getAll()).then((all) => { mem = all || []; }).catch(() => { loaded = null; });
  return loaded;
}

export async function queueWrite(w: QueuedWrite): Promise<void> {
  await loadQueue();
  const seq = await tx<IDBValidKey>('readwrite', (s) => s.add(w));
  mem.push({ ...w, seq: seq as number });
}

/** A plugin's waiting writes, oldest first (so it can show them over the saved data). */
export function pendingWrites(plugin: string): QueuedWrite[] {
  return mem.filter((w) => w.plugin === plugin).map((w) => ({ ...w }));
}

export const queuedCount = (): number => mem.length;

/** Called after a plugin's writes went in (it reloads what it shows). */
export function onSynced(plugin: string, fn: () => void): () => void {
  if (!synced.has(plugin)) synced.set(plugin, new Set());
  synced.get(plugin)!.add(fn);
  return () => synced.get(plugin)?.delete(fn);
}

const unreachable = (e: unknown) => [0, 502, 503, 504].includes((e as ApiError | null)?.status ?? 0);

let flushing: Promise<void> | null = null;
export function flushQueue(names: (id: string) => string = (id) => id): Promise<void> {
  flushing ||= doFlush(names).finally(() => { flushing = null; });
  return flushing;
}

async function doFlush(names: (id: string) => string): Promise<void> {
  await loadQueue();
  const done = new Set<string>();
  for (const w of [...mem]) {
    const headers = { 'X-Synapse-Offline': String(w.at) };
    try {
      await api.pluginRoute(w.plugin, w.method, w.path, w.body, undefined, undefined, headers);
    } catch (e) {
      if (unreachable(e)) return;   // still away: the rest waits, in order
      const err = e as ApiError;
      const c = (err.data as { conflict?: { message?: string; retry?: unknown } } | null)?.conflict;
      if (err.status === 409 && c) {
        const keep = await openDialog({
          title: `${names(w.plugin)}: changed in Synapse too`,
          subtitle: 'While you were offline, this was also changed in Synapse.',
          dismissable: false,
          render: (b) => { b.innerHTML = `<p style="margin:0">${escapeHtml(c.message || err.message)}</p>`; },
          buttons: [{ label: 'Keep the version in Synapse', value: 'theirs' }, { label: 'Keep mine', kind: 'primary', value: 'mine' }],
        });
        if (keep === 'mine' && c.retry) {
          try { await api.pluginRoute(w.plugin, w.method, w.path, c.retry, undefined, undefined, { ...headers, 'X-Synapse-Offline-Force': '1' }); } catch (e2) {
            if (unreachable(e2)) return;
            toast(`${names(w.plugin)}: ${(e2 as Error).message}`, { type: 'error', timeout: 8000 });
          }
        }
      } else {
        toast(`${names(w.plugin)}: a change made offline was not accepted: ${err.message}`, { type: 'error', timeout: 8000 });
      }
    }
    await tx('readwrite', (s) => s.delete(w.seq!));
    mem = mem.filter((x) => x.seq !== w.seq);
    done.add(w.plugin);
  }
  for (const p of done) for (const fn of synced.get(p) || []) { try { fn(); } catch (e) { console.error(e); } }
}

/** Deletes every waiting write (offline use turned off). */
export async function removeQueue(): Promise<void> {
  mem = [];
  loaded = null;
  await new Promise<void>((done) => {
    try {
      const del = indexedDB.deleteDatabase(DB);
      del.onsuccess = del.onerror = del.onblocked = () => done();
    } catch { done(); }
  });
}
