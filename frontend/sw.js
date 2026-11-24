// Synapse's service worker: keeps the app usable on a bad connection (docs/offline.md).
//
// Reading: every GET the app makes is saved in the Cache Storage when it succeeds. Pages,
// scripts and data are fetched from the network first (with a timeout) and fall back to the
// saved copy; PDFs come from the saved copy first, since a file does not change.
// Annotations: a create/change/delete that cannot reach the server is queued in IndexedDB,
// answered with 202 at once, applied to the saved annotation list, and sent once the server
// answers again (in order).
'use strict';

const CACHE = 'synapse-offline-v1';   // keep the name: renaming it would drop what was saved
const TIMEOUT_MS = 6000;
const DB = 'synapse-offline';
const STORE = 'outbox';

const NEVER = [/^\/api\/changes/, /^\/api\/presence/, /^\/sw\.js$/, /^\/mcp/, /^\/api\/passkeys/, /^\/api\/devices/,
  /^\/api\/backups/, /^\/api\/updates/];
const PDF = [/^\/pdfs\//, /^\/api\/attachments\/\d+\/remote$/, /^\/api\/note-pdfs\/\d+\/file/];
const ANNO_WRITE = [/^\/api\/(attachments|note-pdfs)\/\d+\/annotations$/, /^\/api\/annotations\/[^/]+$/];

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('message', (e) => {
  if (e.data === 'flush') e.waitUntil(flush());
  if (e.data === 'status') e.waitUntil(broadcast());
  if (e.data && e.data.keep) e.waitUntil(keep(e.data.keep, !!e.data.quiet));
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const path = url.pathname;
  if (NEVER.some((r) => r.test(path))) return;
  if (req.method === 'GET') {
    if (req.headers.get('range')) return;            // the viewer asks for whole files (disableRange)
    if (req.headers.get('accept') === 'text/event-stream') return;
    e.respondWith(PDF.some((r) => r.test(path)) ? cacheFirst(req) : networkFirst(req));
    return;
  }
  if (ANNO_WRITE.some((r) => r.test(path))) e.respondWith(write(req));
});

// ------------------------------------------------------------------------- reading
function timeout(ms) { return new Promise((_, no) => setTimeout(() => no(new Error('timeout')), ms)); }

async function save(req, res) {
  if (res.ok && res.status === 200 && res.type === 'basic' && !res.redirected) {
    const c = await caches.open(CACHE);
    await c.put(req, res.clone());
  }
  return res;
}

async function saved(req) {
  const c = await caches.open(CACHE);
  const hit = await c.match(req);
  if (hit) return hit;
  // A page (viewer.html?file=…) works with any query: its script reads the query itself.
  if (req.mode === 'navigate' || /\.html$/.test(new URL(req.url).pathname)) return c.match(req, { ignoreSearch: true });
  return undefined;
}

async function networkFirst(req) {
  try {
    const res = await Promise.race([fetch(req), timeout(TIMEOUT_MS)]);
    flushSoon();
    return save(req, res);
  } catch (err) {
    const hit = await saved(req);
    if (hit) return hit;
    return offlineAnswer(req);
  }
}

async function cacheFirst(req) {
  const hit = await saved(req);
  if (hit) return hit;
  try { return await save(req, await fetch(req)); } catch { return offlineAnswer(req); }
}

function offlineAnswer(req) {
  const isApi = new URL(req.url).pathname.startsWith('/api/');
  if (isApi || req.destination === '') {
    return new Response(JSON.stringify({ error: 'Offline, and this was not saved for offline use.' }),
      { status: 503, headers: { 'Content-Type': 'application/json', 'X-Synapse-Offline': '1' } });
  }
  return new Response('Offline, and this page was not saved for offline use.',
    { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8', 'X-Synapse-Offline': '1' } });
}

/** Saves a list of URLs (PDFs, pages) for offline use, four at a time, each given at most 30 s;
 *  reports progress to the pages unless asked to be quiet (the automatic saving after a visit). */
async function keep(urls, quiet = false) {
  let done = 0, failed = 0, next = 0;
  const one = async (u) => {
    const path = new URL(u, self.location.origin).pathname;
    if (NEVER.some((r) => r.test(path))) return;
    const req = new Request(u, { credentials: 'same-origin' });
    if (await saved(req)) return;
    const res = await Promise.race([fetch(req), timeout(30000)]);
    if (!res.ok) throw new Error(String(res.status));
    await save(req, res);
  };
  const worker = async () => {
    while (next < urls.length) {
      const u = urls[next++];
      try { await one(u); done++; } catch { failed++; }
      if (!quiet) await post({ type: 'keep-progress', done, failed, total: urls.length });
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
}

// ------------------------------------------------------------------------- the outbox
function idb() {
  return new Promise((ok, no) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: 'seq', autoIncrement: true });
    r.onsuccess = () => ok(r.result);
    r.onerror = () => no(r.error);
  });
}
async function tx(mode, fn) {
  const db = await idb();
  return new Promise((ok, no) => {
    const t = db.transaction(STORE, mode);
    const out = fn(t.objectStore(STORE));
    t.oncomplete = () => ok(out && 'result' in out ? out.result : undefined);
    t.onerror = () => no(t.error);
  });
}
const queued = () => tx('readonly', (s) => s.getAll());
const enqueue = (entry) => tx('readwrite', (s) => s.add(entry));
const dequeue = (seq) => tx('readwrite', (s) => s.delete(seq));

async function write(req) {
  const body = req.method === 'DELETE' ? null : await req.clone().text();
  const pending = await queued();
  if (!pending.length) {   // nothing waiting: try the server first (keeps the order otherwise)
    try {
      const res = await Promise.race([fetch(req), timeout(TIMEOUT_MS)]);
      return res;
    } catch { /* offline: queue it */ }
  }
  const entry = { method: req.method, url: req.url, body, client: req.headers.get('X-Synapse-Client') || '', at: Date.now() };
  await enqueue(entry);
  const annotation = await applyLocally(entry);
  broadcast();
  flushSoon();
  return new Response(JSON.stringify(req.method === 'DELETE' ? { ok: true, queued: true } : { annotation, queued: true }),
    { status: 202, headers: { 'Content-Type': 'application/json', 'X-Synapse-Queued': '1' } });
}

/** Applies a queued change to the saved annotation lists, so a reload offline shows it. */
async function applyLocally(entry) {
  const c = await caches.open(CACHE);
  const path = new URL(entry.url).pathname;
  const data = entry.body ? JSON.parse(entry.body) : {};
  if (entry.method === 'POST') {
    const listUrl = new URL(entry.url).origin + path;
    await editList(c, listUrl, (list) => list.filter((a) => a.id !== data.id).concat([data]));
    return data;
  }
  const id = decodeURIComponent(path.split('/').pop());
  let result = { id, ...data };
  for (const req of await c.keys()) {
    if (!/\/annotations$/.test(new URL(req.url).pathname)) continue;
    await editList(c, req.url, (list) => {
      const i = list.findIndex((a) => a.id === id);
      if (i < 0) return null;
      if (entry.method === 'DELETE') return list.filter((a) => a.id !== id);
      list[i] = result = { ...list[i], ...data };
      return list;
    });
  }
  return result;
}

async function editList(c, url, fn) {
  const hit = await c.match(url);
  if (!hit) return;
  let json;
  try { json = await hit.json(); } catch { return; }
  const next = fn([...(json.annotations || [])]);
  if (!next) return;
  await c.put(url, new Response(JSON.stringify({ ...json, annotations: next }),
    { status: 200, headers: { 'Content-Type': 'application/json' } }));
}

let flushing = null;
let flushTimer = null;
function flushSoon() { if (!flushTimer) flushTimer = setTimeout(() => { flushTimer = null; flush(); }, 1500); }

function flush() {
  if (!flushing) flushing = doFlush().finally(() => { flushing = null; });
  return flushing;
}

async function doFlush() {
  const list = await queued();
  if (!list.length) return;
  let sent = 0, dropped = 0;
  for (const e of list) {
    const headers = { 'X-Synapse-Client': e.client };
    if (e.body) headers['Content-Type'] = 'application/json';
    let res;
    try {
      res = await Promise.race([fetch(e.url, { method: e.method, headers, body: e.body, credentials: 'same-origin' }), timeout(15000)]);
    } catch { break; }                                  // still offline: keep the rest, in order
    if (res.status >= 500 || res.status === 401 || res.status === 403) break;  // try again later
    if (!res.ok) dropped++;                             // 404 etc.: the server refused it; don't retry forever
    else sent++;
    await dequeue(e.seq);
  }
  await broadcast({ sent, dropped });
  if ((await queued()).length) setTimeout(flush, 30000);
}

async function post(msg) {
  for (const c of await self.clients.matchAll({ includeUncontrolled: true })) c.postMessage(msg);
}
async function broadcast(extra = {}) {
  await post({ type: 'outbox', waiting: (await queued()).length, ...extra });
}
