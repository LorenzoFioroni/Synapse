// Synapse's service worker: keeps the app usable on a bad connection (docs/offline.md). The page
// registers it only when the user turned on Settings → Offline Use, and removes it when it is turned off.
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

// Never saved: streams, secrets, and note texts (a locked note must not outlive its lock in a cache).
const NEVER = [/^\/api\/changes/, /^\/api\/presence/, /^\/sw\.js$/, /^\/mcp/, /^\/api\/passkeys/, /^\/api\/devices/,
  /^\/api\/backups/, /^\/api\/updates/, /^\/api\/notes\/(note|history|links|lock|locks|unlock|relock)/, /^\/api\/companion/, /^\/api\/mcp\//];
const PDF = [/^\/pdfs\//, /^\/api\/attachments\/\d+\/remote$/, /^\/api\/note-pdfs\/\d+\/file/];
const ANNO_WRITE = [/^\/api\/(attachments|note-pdfs)\/\d+\/annotations$/, /^\/api\/annotations\/[^/]+$/];

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

let off = false;   // turned off in Settings: this worker only waits for its pages to close

self.addEventListener('message', (e) => {
  if (e.data === 'off') off = true;
  if (e.data === 'flush') e.waitUntil(flush());
  if (e.data === 'status') e.waitUntil(broadcast().then(() => post({ type: 'reach', up: reachable })));
  if (e.data && e.data.keep) e.waitUntil(keep(e.data));
  if (e.data && e.data.prune) e.waitUntil(prune(e.data.prune));
});

self.addEventListener('fetch', (e) => {
  if (off) return;
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
  if (!off && res.ok && res.status === 200 && res.type === 'basic' && !res.redirected) {
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

let reachable = true;
/** Tells the pages when the server stops or starts answering (the browser can be online and the server not). */
function reach(up) { if (up !== reachable) { reachable = up; post({ type: 'reach', up }); } }

async function networkFirst(req) {
  try {
    const res = await Promise.race([fetch(req), timeout(TIMEOUT_MS)]);
    reach(true);
    flushSoon();
    return save(req, res);
  } catch (err) {
    reach(false);
    const hit = await saved(req);
    if (hit) return hit;
    return offlineAnswer(req);
  }
}

async function cacheFirst(req) {
  const hit = await saved(req);
  if (hit) return hit;
  try { return await save(req, await fetch(req)); } catch { reach(false); return offlineAnswer(req); }
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

/** Saves a list of URLs (pages, scripts, data, PDFs) for offline use, four at a time, each given at
 *  most 30 s, and tells the pages how many worked (``id`` says which request). With ``refresh`` a
 *  URL already saved is fetched again. The page sends short lists one after another, so a long
 *  save never depends on this worker staying alive. */
async function keep({ keep: urls, id, refresh }) {
  let done = 0, failed = 0, next = 0;
  const one = async (u) => {
    const path = new URL(u, self.location.origin).pathname;
    if (NEVER.some((r) => r.test(path))) return;
    const req = new Request(u, { credentials: 'same-origin' });
    if (!refresh && await saved(req)) return;
    const res = await Promise.race([fetch(req), timeout(30000)]);
    if (!res.ok) throw new Error(String(res.status));
    await save(req, res);
  };
  const worker = async () => {
    while (next < urls.length) {
      const u = urls[next++];
      try { await one(u); done++; } catch { failed++; }
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
  await post({ type: 'keep-done', id, done, failed });
}

/** Forgets saved PDFs and annotation lists that are not in ``allowed`` (the library no longer has them). */
async function prune(allowed) {
  const keepSet = new Set(allowed);
  const c = await caches.open(CACHE);
  for (const req of await c.keys()) {
    const u = new URL(req.url);
    const mine = PDF.some((r) => r.test(u.pathname)) || /\/annotations$/.test(u.pathname);
    if (mine && !keepSet.has(u.pathname + u.search)) await c.delete(req);
  }
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
    t.oncomplete = () => { db.close(); ok(out && 'result' in out ? out.result : undefined); };   // an open connection would block deleting the database
    t.onerror = () => { db.close(); no(t.error); };
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
    } catch { reach(false); /* offline: queue it */ }
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
  if (off) return Promise.resolve();   // turned off: opening the database would create it again
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
  if (off) return;
  await post({ type: 'outbox', waiting: (await queued()).length, ...extra });
}
