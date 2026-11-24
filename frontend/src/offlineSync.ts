// Saving the library for offline use (the main page only; offline.ts is shared with the viewer).
// Turning offline use on saves the app's files, the library and every PDF in the browser; the app
// then repeats it quietly when it starts, so new papers are saved too. See docs/offline.md.
import { api, fileUrl } from './api.js';
import { OFFLINE_EVENT, offlineEnabled, offlineSupported, pdfSaved, pruneFiles, refreshSaved, removeOfflineCopy, saveFiles, setOfflineFlag, startOffline } from './offline.js';
import { storage, store } from './util.js';

export const SYNC_EVENT = 'zc:offline-sync';
const VERSION_KEY = 'zc.offline.shell';   // the app version that was saved
const DONE_KEY = 'zc.offline.synced';     // when everything was last saved
const CHUNK = 8;                          // files per request to the worker: it may stop between requests

export interface SyncState {
  running: boolean;
  /** What is being saved now: "App files", "Library" or "PDFs". */
  step: string;
  done: number;
  total: number;
  failed: number;
}

export const syncState: SyncState = { running: false, step: '', done: 0, total: 0, failed: 0 };

/** The result of the last complete save, kept across reloads: when, the PDFs of the library, how many are saved, how many failed. */
export interface SyncResult { at: number; pdfs: number; saved: number; failed: number }
export const lastSynced = (): SyncResult | null => storage<SyncResult | null>(DONE_KEY, null);

const tick = () => window.dispatchEvent(new Event(SYNC_EVENT));

/** Saves ``urls`` a few at a time, counting them into ``syncState``. */
async function saveAll(step: string, urls: string[], refresh = false): Promise<number> {
  Object.assign(syncState, { step, done: 0, total: urls.length });
  tick();
  let failed = 0;
  for (let i = 0; i < urls.length; i += CHUNK) {
    if (!offlineEnabled()) throw new Error('Offline use was turned off.');
    const r = await saveFiles(urls.slice(i, i + CHUNK), refresh);
    failed += r.failed;
    syncState.done = Math.min(urls.length, i + CHUNK);
    syncState.failed += r.failed;
    tick();
  }
  return failed;
}

/** The data the app asked for while starting (settings, folders, tags…): it is saved again so it is there offline. */
function startupData(): string[] {
  return [...new Set(performance.getEntriesByType('resource')
    .filter((e) => (e as PerformanceResourceTiming).initiatorType === 'fetch')
    .map((e) => new URL(e.name, location.href))
    .filter((u) => u.origin === location.origin && u.pathname.startsWith('/api/') && !u.pathname.startsWith('/api/items/'))
    .map((u) => u.pathname + u.search))];
}

let running: Promise<void> | null = null;

/** Saves what is missing (or, with ``all``, everything again). Calls while one runs share it. */
export function syncOffline(all = false): Promise<void> {
  if (!offlineEnabled() || !navigator.onLine) return Promise.resolve();
  running ||= run(all).finally(() => { running = null; syncState.running = false; tick(); });
  return running;
}

async function run(all: boolean): Promise<void> {
  Object.assign(syncState, { running: true, failed: 0 });
  tick();
  // 1. the app itself
  const shell = await fetch('/api/offline/shell', { cache: 'no-store' }).then((r) => r.json() as Promise<{ version: string; files: string[] }>);
  const stale = all || storage<string>(VERSION_KEY, '') !== shell.version;
  const appFailed = await saveAll('App files', shell.files, stale);
  if (!appFailed && offlineEnabled()) store(VERSION_KEY, shell.version);
  // 2. the library, and what the app needs to show it
  Object.assign(syncState, { step: 'Library', done: 0, total: 0 });
  tick();
  const [library, notes] = await Promise.all([api.listItems({ view: 'library' }), api.notes().catch(() => null)]);
  await saveFiles(startupData(), true);
  const items = library.items.filter((it) => !it._outside);
  await saveAll('Library', items.map((it) => `/api/items/${it.id}`));
  // 3. the PDFs, with their annotations
  const pdfs: { url: string; notes: string }[] = [];
  for (const it of items) {
    for (const a of it.attachments || []) if (a.type === 'pdf') pdfs.push({ url: fileUrl(a), notes: `/api/attachments/${a.id}/annotations` });
  }
  for (const p of notes?.pdfs || []) pdfs.push({ url: `/api/note-pdfs/${p.id}/file`, notes: `/api/note-pdfs/${p.id}/annotations` });
  const files = pdfs.flatMap((p) => [p.url, p.notes]);
  await saveAll('PDFs', files);
  await pruneFiles(files);
  await refreshSaved();
  if (offlineEnabled()) store(DONE_KEY, { at: Date.now(), pdfs: pdfs.length, saved: pdfs.filter((p) => pdfSaved(p.url)).length, failed: syncState.failed });
}

/** Turns offline use on: registers the worker and saves everything. Rejects when this browser cannot do it. */
export async function enableOffline(): Promise<void> {
  if (!offlineSupported()) throw new Error('Offline use needs a secure page: open Synapse over https, or on this computer as localhost.');
  setOfflineFlag(true);
  try {
    await startOffline();
  } catch (e) { setOfflineFlag(false); throw e; }
  window.dispatchEvent(new Event(OFFLINE_EVENT));
  await syncOffline(true).catch((e) => { if (offlineEnabled()) throw e; });   // turned off meanwhile: not an error
}

export async function disableOffline(): Promise<void> {
  await removeOfflineCopy();
  await running?.catch(() => null);   // the save that was running notices and stops
  Object.assign(syncState, { running: false, step: '', done: 0, total: 0, failed: 0 });
  tick();
}

// Quietly, a little after the app has started: new papers, and a new version of the app, get saved too.
if (offlineEnabled()) setTimeout(() => { syncOffline().catch(() => null); }, 8000);
window.addEventListener('online', () => { if (offlineEnabled()) setTimeout(() => { syncOffline().catch(() => null); }, 3000); });
