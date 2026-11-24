// Offline use, page side (the worker is frontend/sw.js; see docs/offline.md). Offline use is off
// until the user turns it on in Settings → Offline Use, per browser: only then is the worker
// registered. This file starts and stops it, knows which PDFs it has saved, tells whether Synapse
// can be reached, shows a small pill while it cannot, and asks the worker to send annotation changes
// made meanwhile. The viewer imports it too, so it has no other dependencies.

const KEY = 'zc.offline';
const CACHE = 'synapse-offline-v1';   // as in sw.js
export const OFFLINE_EVENT = 'zc:offline';   // reachability, the setting or the saved files changed

type OutboxMsg = { type: 'outbox'; waiting: number; sent?: number; dropped?: number };
type DoneMsg = { type: 'keep-done'; id: number; done: number; failed: number };
type ReachMsg = { type: 'reach'; up: boolean };

const inFrame = typeof window !== 'undefined' && window.top !== window;   // the viewer runs in an iframe: the main page shows the pill
let waiting = 0;
let serverUp = true;
let saved = new Set<string>();
let pill: HTMLElement | null = null;
let probe: number | undefined;
const asked = new Map<number, (r: { done: number; failed: number }) => void>();
let nextId = Math.floor(Math.random() * 1e9);   // several windows may ask the same worker

export const offlineSupported = (): boolean => typeof window !== 'undefined' && 'serviceWorker' in navigator && window.isSecureContext && 'caches' in window;

export function offlineEnabled(): boolean {
  try { return localStorage.getItem(KEY) === '1'; } catch { return false; }
}

/** True while Synapse cannot be reached and the saved copies are what the app shows. */
export const offlineNow = (): boolean => offlineEnabled() && (!navigator.onLine || !serverUp);

/** Whether the PDF at ``url`` (as the app opens it, e.g. ``/pdfs/x.pdf``) is saved for offline use. */
export const pdfSaved = (url: string): boolean => saved.has(url);

export const changesWaiting = (): number => waiting;

const changed = () => window.dispatchEvent(new Event(OFFLINE_EVENT));

/** Reads which files the worker has saved. */
export async function refreshSaved(): Promise<void> {
  if (!offlineEnabled()) { saved = new Set(); changed(); return; }   // opening the cache would create it again
  try {
    const cache = await caches.open(CACHE);
    saved = new Set((await cache.keys()).map((r) => { const u = new URL(r.url); return u.pathname + u.search; }));
  } catch { saved = new Set(); }
  changed();
}

function show(text: string | null): void {
  if (inFrame) return;
  if (!text) { pill?.remove(); pill = null; return; }
  if (!pill) {
    pill = document.createElement('div');
    pill.className = 'zc-offline-pill';
    pill.setAttribute('role', 'status');
    document.body.appendChild(pill);
  }
  pill.textContent = text;
}

function refresh(): void {
  if (!offlineEnabled()) { show(null); return; }
  const w = waiting ? ` · ${waiting} annotation change${waiting === 1 ? '' : 's'} waiting` : '';
  if (offlineNow()) show(`${navigator.onLine ? 'Synapse can’t be reached' : 'Offline'} — saved papers still open${w}`);
  else if (waiting) show(`Sending${w}…`);
  else show(null);
}

function post(msg: unknown): void {
  navigator.serviceWorker?.controller?.postMessage(msg);
}

/** Sends the worker a message even before it controls this page (right after registering). */
export async function tellWorker(msg: unknown): Promise<void> {
  if (!offlineEnabled()) return;   // `ready` never settles once the worker is gone
  const reg = await navigator.serviceWorker.ready;
  (navigator.serviceWorker.controller || reg.active)?.postMessage(msg);
}

/** Asks the worker to save ``urls`` (it fetches them itself) and resolves with how many it could. */
export function saveFiles(urls: string[], refreshAll = false): Promise<{ done: number; failed: number }> {
  if (!urls.length || !offlineEnabled()) return Promise.resolve({ done: 0, failed: 0 });
  const id = nextId++;
  return new Promise((resolve) => {
    asked.set(id, resolve);
    tellWorker({ keep: urls, id, refresh: refreshAll });
  });
}

export const pruneFiles = (allowed: string[]): Promise<void> => tellWorker({ prune: allowed });

function checkServer(): void {
  // /sw.js is never answered from the saved copies, so this asks the server itself.
  const again = () => { window.clearTimeout(probe); probe = window.setTimeout(checkServer, 15000); };
  if (serverUp || !offlineEnabled()) return;
  fetch('/sw.js', { method: 'HEAD', cache: 'no-store' })
    .then((r) => { if (r.ok) { serverUp = true; changed(); refresh(); post('flush'); } else again(); })
    .catch(again);
}

function onMessage(e: MessageEvent<OutboxMsg | DoneMsg | ReachMsg>): void {
  const m = e.data;
  if (m?.type === 'outbox') {
    waiting = m.waiting;
    refresh();
    changed();
    if (m.dropped && !inFrame) show(`${m.dropped} queued change(s) were refused by the server`);
  } else if (m?.type === 'keep-done') {
    asked.get(m.id)?.({ done: m.done, failed: m.failed });
    asked.delete(m.id);
  } else if (m?.type === 'reach') {
    serverUp = m.up;
    refresh();
    changed();
    if (!m.up) { window.clearTimeout(probe); probe = window.setTimeout(checkServer, 15000); }
  }
}

let listening = false;

/** Registers the worker and starts listening to it. Call after the setting was turned on. */
export async function startOffline(): Promise<void> {
  if (!offlineSupported()) throw new Error('Offline use needs a secure page: https, or localhost.');
  if (!listening) {
    listening = true;
    navigator.serviceWorker.addEventListener('message', onMessage as EventListener);
    window.addEventListener('online', () => { refresh(); changed(); post('flush'); checkServer(); });
    window.addEventListener('offline', () => { void refreshSaved(); refresh(); });
  }
  await navigator.serviceWorker.register('/sw.js', { scope: '/' });
  await navigator.serviceWorker.ready;
  post('status');
  await refreshSaved();
  refresh();
}

export function setOfflineFlag(on: boolean): void {
  try { if (on) localStorage.setItem(KEY, '1'); else localStorage.removeItem(KEY); } catch { /* private mode */ }
}

/** Forgets everything offline use saved on this device: the worker, the saved files, the queue,
 *  what was remembered in this browser, and any save that was still running. */
export async function removeOfflineCopy(): Promise<void> {
  setOfflineFlag(false);
  serverUp = true; waiting = 0; saved = new Set();
  window.clearTimeout(probe);
  for (const resolve of asked.values()) resolve({ done: 0, failed: 0 });   // a save in progress stops
  asked.clear();
  try {
    for (const reg of await navigator.serviceWorker.getRegistrations()) {
      reg.active?.postMessage('off');   // it keeps serving open pages until they close: it must stop saving
      await reg.unregister();
    }
  } catch { /* nothing registered */ }
  try { await caches.delete(CACHE); } catch { /* none */ }
  await new Promise<void>((done) => {
    try {
      const del = indexedDB.deleteDatabase('synapse-offline');
      del.onsuccess = del.onerror = del.onblocked = () => done();
    } catch { done(); }
  });
  try { for (const k of ['zc.offline.shell', 'zc.offline.synced']) localStorage.removeItem(k); sessionStorage.removeItem('zc.offline.seeded'); } catch { /* private mode */ }
  refresh();
  changed();
}

if (offlineEnabled()) {
  if (offlineSupported()) {
    startOffline().catch((e) => console.warn('[offline] service worker:', e));
  } else setOfflineFlag(false);
} else if (offlineSupported() && !inFrame) {
  // Off by default: a worker left by an earlier build, or by turning this off elsewhere, is removed.
  navigator.serviceWorker.getRegistrations().then((regs) => { if (regs.length) void removeOfflineCopy(); }).catch(() => null);
}

export {};
