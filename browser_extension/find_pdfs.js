// "Find Missing PDFs": fetches the publisher's PDF of library items that have none, in this
// browser. Each DOI opens in one background tab, so the institution's access (campus network,
// VPN, the user's sign-ins) and the publishers' browser checks work as when the user opens the
// paper; the PDF is fetched from inside that page, as the Save button does, and sent to the
// library, which checks it is the paper before attaching it (POST /api/connector/attach-pdf).
// One paper at a time with a pause between them: publishers block institutions that download
// in bulk.
import { fetchPdfInPage, pageState } from './extract.js';
import { loadConnection, libraryFetch } from './connection.js';

/** @typedef {import('./extract.js').PageState} PageState */
/** @typedef {{ id: string; title: string; doi: string; year?: number | null; cursor?: string }} Missing */
/** @typedef {'attached' | 'wrong-paper' | 'unverified' | 'has-pdf' | 'no-access' | 'no-link' | 'blocked' | 'error'} Outcome */
/** @typedef {{ outcome: Outcome; why?: string }} Found  what happened, and the details shown under the row */

const params = new URLSearchParams(location.search);
// The automated tests point the DOIs at a local page instead of doi.org.
const RESOLVER = params.get('resolver') || 'https://doi.org/';
const PAUSE_MS = Number(params.get('pause') ?? 5000);   // between papers
const LOAD_MS = 45000;      // one navigation
const SETTLE_MS = 25000;    // a browser check, redirects by script
const POLL_MS = 1000;
const STEADY_POLLS = 4;     // an article page without PDF links: polls without change before giving up
const MAX_PDF_BYTES = 60 * 1024 * 1024;
const BATCH = Math.max(1, Math.min(50, Number(params.get('batch')) || 50));
const OPEN_LINKS = 3;       // links opened in the tab, at most (each can take a while)
const requestedIds = params.get('ids') || '';
const fallback = params.get('fallback') === '1';

/** @type {Record<Outcome, [string, 'ok' | 'warn' | 'err']>} */
const OUTCOMES = {
  attached: ['PDF attached ✓', 'ok'],
  'has-pdf': ['Already has a PDF', 'ok'],
  'wrong-paper': ['A PDF came back, but not of this paper', 'warn'],
  unverified: ['PDF needs review: its identity could not be confirmed', 'warn'],
  'no-access': ['No access: the PDF links gave a page, not the PDF', 'warn'],
  'no-link': ['No PDF link found on the article page', 'warn'],
  blocked: ['The site’s browser check did not finish: open the paper once in this browser, then Retry', 'warn'],
  error: ['Error', 'err'],
};

/**
 * @template {HTMLElement} T
 * @param {string} id
 * @param {new () => T} type
 * @returns {T}
 */
const get = (id, type) => { const e = document.getElementById(id); if (!(e instanceof type)) throw new Error(`#${id} is missing`); return e; };
/** @type {Record<string, string>} */
const ENTITIES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
/** @param {unknown} s */
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ENTITIES[c]);
/** @param {number} ms */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const view = get('view', HTMLElement);
const conn = get('conn', HTMLElement);
let server = '';
let token = '';
/** @type {{ id: number; name: string; parent_id: number | null }[]} */
let folders = [];
/** @type {Missing[]} */
let items = [];
/** @type {Map<string, Outcome>} */
const outcomes = new Map();
let running = false;
let stopping = false;
/** @type {string | null} */
let nextCursor = null;
let total = 0;
let loadedFolder = '';
let loadSeq = 0;

const progressKey = () => JSON.stringify([server, loadedFolder]);
/** @param {string | null} cursor */
async function saveProgress(cursor) {
  if (requestedIds) return;
  // Separate storage keys avoid read/modify/write races between windows and capture this folder
  // before any asynchronous work can let navigation select another one.
  const key = `pdfProgress:${progressKey()}`;
  if (cursor) await chrome.storage.local.set({ [key]: cursor }); else await chrome.storage.local.remove(key);
}

/**
 * @param {string} path
 * @param {RequestInit} [opts]
 * @returns {Promise<{ ok: boolean; status: number; data: any }>}
 */
async function api(path, opts = {}) {
  const res = await libraryFetch(server, token, path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  return { ok: res.ok, status: res.status, data };
}

async function init() {
  try {
    ({ server, token } = await loadConnection());
    if (params.has('library') && params.get('library') !== server) {
      throw new Error('The library connection changed. Start the PDF request again from the intended library.');
    }
    const ping = await api('/api/connector/ping');
    if (!ping.ok) throw new Error(ping.data?.error || `Error ${ping.status}`);
    if (requestedIds && !ping.data.capabilities?.publisher_pdfs) throw new Error('Update Synapse to search the items selected in the app.');
    folders = ping.data.folders || [];
  } catch (e) {
    conn.className = 'conn off';
    view.innerHTML = `<div class="result"><div class="big err">!</div><h2>Library unavailable</h2>
      <p>${esc(e instanceof Error ? e.message : e)}</p><p>Check the extension’s settings, then reopen this window.</p></div>`;
    return;
  }
  conn.className = 'conn ok';
  conn.title = `Connected to ${server}`;
  await renderForm();
  // A library request can start unattended only after the user granted publisher access before.
  if (params.get('auto') === '1' && items.length) void start(items, { automatic: true });
}

/** @param {string} selected */
function folderOptions(selected) {
  /** @type {Record<string, typeof folders>} */
  const kids = {};
  for (const f of folders) (kids[f.parent_id ?? 'root'] ||= []).push(f);
  const out = ['<option value="">All items</option>'];
  /** @type {(p: string | number, depth: number) => void} */
  const walk = (p, depth) => (kids[p] || []).sort((a, b) => a.name.localeCompare(b.name)).forEach((f) => {
    out.push(`<option value="${f.id}" ${String(f.id) === selected ? 'selected' : ''}>${' '.repeat(depth)}${esc(f.name)}</option>`);
    walk(f.id, depth + 1);
  });
  walk('root', 0);
  return out.join('');
}

/** @param {string} [folder] @param {string | null} [cursor] */
async function renderForm(folder = '', cursor = undefined) {
  const seq = ++loadSeq;
  loadedFolder = folder;
  view.innerHTML = `
    <p class="intro">Opens each paper’s DOI in a background tab of this browser and attaches the publisher’s PDF,
      using the access this browser has (campus network, VPN, your sign-ins). Keep this window open while it runs.</p>
    ${requestedIds ? '<p class="hint">Selected by Synapse when adding items. Publisher access is tried first; other enabled PDF sources follow if needed.</p>' : ''}
    <label class="f" for="folder">Items without a PDF in</label>
    <select id="folder" ${requestedIds ? 'disabled' : ''}>${folderOptions(folder)}</select>
    <div id="list"><div class="center"><span class="spinner"></span> Loading…</div></div>`;
  get('folder', HTMLSelectElement).onchange = (e) => {
    if (e.target instanceof HTMLSelectElement) renderForm(e.target.value);
  };
  if (cursor === undefined && !requestedIds) {
    const key = `pdfProgress:${progressKey()}`;
    const saved = await chrome.storage.local.get(key);
    cursor = typeof saved[key] === 'string' ? saved[key] : null;
  }
  const q = new URLSearchParams({ limit: String(BATCH), ...(folder ? { folder_id: folder } : {}),
    ...(requestedIds ? { ids: requestedIds } : {}), ...(cursor ? { cursor } : {}) });
  const res = await api(`/api/connector/missing-pdfs?${q}`);
  if (seq !== loadSeq) return;
  const list = get('list', HTMLElement);
  if (res.status === 404) {
    // People update the extension and the library separately: a library from before this page.
    view.innerHTML = `<div class="result"><div class="big warn">!</div><h2>Update Synapse first</h2>
      <p>The library at <code>${esc(server)}</code> is older than this extension and cannot find missing PDFs yet.</p>
      <p>Update it (Settings → Updates, or pull the code and restart the server), then reopen this window.</p></div>`;
    return;
  }
  if (!res.ok) {
    list.innerHTML = `<p class="summary">${esc(res.data?.error || `Error ${res.status}`)}</p>`;
    return;
  }
  items = res.data.items;
  nextCursor = res.data.next_cursor || null;
  total = res.data.total;
  outcomes.clear();
  if (!items.length) {
    if (cursor && total) { await saveProgress(null); return renderForm(folder, null); }
    list.innerHTML = '<p class="summary">Every item with a DOI here has a PDF.</p>';
    return;
  }
  const more = total > items.length ? ` (${items.length} in this batch of ${total}; progress is remembered)` : '';
  list.innerHTML = `
    <p class="summary">${items.length} item${items.length === 1 ? '' : 's'} with a DOI and no PDF${esc(more)}.</p>
    <div class="actions"><button class="btn primary" id="start">Find PDFs</button><button class="btn" id="stop" disabled>Stop after this paper</button></div>
    <div class="actions"><button class="btn" id="next" disabled>Next batch</button><button class="btn" id="restart">Start from newest</button></div>
    <div class="summary run-progress" role="status" aria-live="polite"></div>
    <div class="hint">One paper every few seconds. If a site asks you to confirm you are human, do it in its tab.</div>
    <ul class="rows">${items.map((it) => `<li data-id="${esc(it.id)}"><span class="what"><b><a href="https://doi.org/${esc(it.doi)}" target="_blank" rel="noopener noreferrer">${esc(it.title || it.doi)}</a></b>
      <small>${esc(it.doi)}${it.year ? ` · ${esc(it.year)}` : ''}</small><small class="why"></small></span><span class="st">Waiting</span></li>`).join('')}</ul>`;
  get('start', HTMLButtonElement).onclick = () => start(items);
  get('stop', HTMLButtonElement).onclick = () => { stopping = true; get('stop', HTMLButtonElement).disabled = true; };
  get('next', HTMLButtonElement).onclick = () => renderForm(folder, nextCursor);
  get('restart', HTMLButtonElement).onclick = async () => { await saveProgress(null); await renderForm(folder, null); };
}

/**
 * @param {string} id
 * @param {string} text
 * @param {'ok' | 'warn' | 'err' | 'busy' | ''} [kind]
 * @param {string} [why]  what the publisher's pages gave, under the item
 */
function setStatus(id, text, kind = '', why = '') {
  const row = [...document.querySelectorAll('.rows li')].find((li) => li instanceof HTMLElement && li.dataset.id === id);
  const st = row?.querySelector('.st');
  if (st) { st.className = `st ${kind}`; st.innerHTML = kind === 'busy' ? `<span class="spinner"></span> ${esc(text)}` : esc(text); }
  const detail = row?.querySelector('.why');
  if (detail && kind !== 'busy') detail.textContent = why;
}

/** @param {Missing[]} todo @param {{automatic?: boolean}} [options] */
async function start(todo, { automatic = false } = {}) {
  if (running) return;
  running = true;
  const startBtn = get('start', HTMLButtonElement);
  startBtn.disabled = true;
  // Chrome requires a user gesture for request(), even when access was granted earlier.
  const permission = { origins: ['https://*/*'] };
  const local = RESOLVER.startsWith('http://127.0.0.1');   // the tests' pages, already allowed
  const allowed = automatic ? await chrome.permissions.contains(permission).catch(() => false)
    : local || await chrome.permissions.request(permission).catch(() => false);
  if (!allowed) {
    running = false;
    startBtn.disabled = false;
    if (!automatic) get('list', HTMLElement).insertAdjacentHTML('afterbegin', '<p class="summary">The extension needs access to publishers’ pages to read the PDF links.</p>');
    return;
  }
  stopping = false;
  get('stop', HTMLButtonElement).disabled = false;
  get('folder', HTMLSelectElement).disabled = true;
  get('next', HTMLButtonElement).disabled = true;
  get('restart', HTMLButtonElement).disabled = true;
  /** @type {number | undefined} */
  let tabId;
  let runError = '';
  try {
    for (const [n, it] of todo.entries()) {
      if (stopping) break;
      get('list', HTMLElement).querySelector('.run-progress')?.replaceChildren(`Paper ${n + 1} of ${todo.length} · publisher first`);
      if (n) await sleep(PAUSE_MS);
      if (stopping) break;
      tabId = await workerTab(tabId);
      /** @type {Found} */
      let found;
      try {
        found = await findOne(tabId, it);
      } catch (e) {
        found = { outcome: 'error', why: String((e instanceof Error && e.message) || e) };
      }
      const firstVisit = !outcomes.has(it.id);
      outcomes.set(it.id, found.outcome);
      const [text, kind] = OUTCOMES[found.outcome];
      setStatus(it.id, text, kind, found.why);
      if (fallback && !['attached', 'has-pdf'].includes(found.outcome)) {
        const queued = await api('/api/connector/find-pdf', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: it.id }),
        });
        if (!queued.ok) {
          outcomes.set(it.id, 'error');
          const message = `Other PDF sources could not start: ${queued.data?.error || `Error ${queued.status}`}`;
          setStatus(it.id, 'Could not start other PDF sources — retry', 'err', message);
          throw new Error(message);
        }
        setStatus(it.id, text, kind, `${found.why || ''} Other enabled PDF sources are now being tried in Synapse.`);
      }
      // Saving the boundary of each completed item also handles Stop/closing halfway through a batch.
      if (firstVisit && it.cursor) await saveProgress(it.cursor);
    }
  } catch (e) {
    runError = `Search paused: ${e instanceof Error ? e.message : String(e)}. You can continue or retry.`;
  } finally {
    if (tabId !== undefined) await chrome.tabs.remove(tabId).catch(() => {});
    running = false;
    await finish(runError);
  }
}

/** @param {string} [error] */
async function finish(error = '') {
  const done = [...outcomes.values()];
  const attached = done.filter((o) => o === 'attached').length;
  const failed = items.filter((it) => outcomes.has(it.id) && !['attached', 'has-pdf'].includes(String(outcomes.get(it.id))));
  const startBtn = get('start', HTMLButtonElement);
  get('stop', HTMLButtonElement).disabled = true;
  const untried = items.filter((it) => !outcomes.has(it.id));
  if (!untried.length && !nextCursor) await saveProgress(null);
  get('folder', HTMLSelectElement).disabled = !!requestedIds;
  startBtn.disabled = !failed.length && !untried.length;
  startBtn.textContent = untried.length ? `Continue ${untried.length} remaining` : failed.length ? `Retry ${failed.length} not found` : 'Done';
  startBtn.onclick = () => start(untried.length ? untried : failed);
  get('restart', HTMLButtonElement).disabled = false;
  get('next', HTMLButtonElement).disabled = !!untried.length || !nextCursor;
  get('next', HTMLButtonElement).textContent = nextCursor ? 'Next batch →' : 'All batches visited';
  get('list', HTMLElement).querySelector('.run-progress')?.replaceChildren(error || (stopping ? 'Stopped. You can continue where you left off.' : nextCursor ? 'Continue with the next batch; failed papers will not block it.' : 'Batch finished. Failed PDFs remain available for review or retry.'));
  get('list', HTMLElement).querySelector('.summary')?.replaceChildren(
    `${attached} PDF${attached === 1 ? '' : 's'} attached of ${done.length} tried.`);
}

/**
 * The tab the papers open in: a background tab of the last browser window, made again if the
 * user closed it.
 * @param {number | undefined} tabId
 * @returns {Promise<number>}
 */
async function workerTab(tabId) {
  if (tabId !== undefined && await chrome.tabs.get(tabId).then(() => true, () => false)) return tabId;
  const win = await chrome.windows.getLastFocused({ windowTypes: ['normal'] }).catch(() => undefined);
  const tab = await chrome.tabs.create({ url: 'about:blank', active: false, ...(win?.id !== undefined ? { windowId: win.id } : {}) });
  if (tab.id === undefined) throw new Error('Could not open a tab.');
  return tab.id;
}

/**
 * Open the paper, find its PDF and hand it to the library.
 * @param {number} tabId
 * @param {Missing} it
 * @returns {Promise<Found>}
 */
async function findOne(tabId, it) {
  setStatus(it.id, 'Opening the article…', 'busy');
  await navigate(tabId, RESOLVER + it.doi.split('/').map(encodeURIComponent).join('/'));
  const page = await settle(tabId);
  if (!page) return { outcome: 'error', why: 'The article page did not load.' };
  if (page.challenge) return { outcome: 'blocked', why: `${short(page.url)} “${page.title}”` };
  const links = page.isPdf ? [page.url] : page.pdfUrls;
  if (!links.length) return { outcome: 'no-link', why: `${short(page.url)} “${page.title}”` };
  setStatus(it.id, 'Getting the PDF…', 'busy');
  const tried = new Set(links);
  /** @type {string[]} */
  const notes = [];
  let wrong = false;
  let unverified = false;
  /**
   * Fetch ``link`` from the page the tab shows and hand a PDF to the library.
   * @param {string} link
   * @returns {Promise<Outcome | undefined>}  attached or has-pdf when done
   */
  const attempt = async (link) => {
    const got = await fetchPdf(tabId, link);
    if (typeof got === 'string') { notes.push(`${short(link)}: ${got}`); return; }
    setStatus(it.id, 'Checking it is the paper…', 'busy');
    const fd = new FormData();
    fd.append('id', it.id);
    fd.append('file', new Blob([got], { type: 'application/pdf' }), 'paper.pdf');
    const res = await api('/api/connector/attach-pdf', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(res.data?.error || `Error ${res.status}`);
    if (res.data.status === 'attached' || res.data.status === 'has-pdf') return res.data.status;
    wrong ||= res.data.status === 'wrong-paper';
    unverified ||= res.data.status === 'unverified';
    notes.push(`${short(link)}: ${res.data.reason || (res.data.status === 'wrong-paper' ? 'a PDF of another paper' : 'not a PDF')}`);
  };
  // From the article page first; opening a link leaves that page, so that comes last.
  for (const link of links) {
    const done = await attempt(link);
    if (done) return { outcome: done };
  }
  // Opened in the tab: a PDF behind a redirect by script (ScienceDirect), or a reader page whose
  // own links lead to the PDF (an Atypon "epdf").
  for (const link of links.slice(0, OPEN_LINKS)) {
    await navigate(tabId, link);
    const opened = await settle(tabId);
    if (!opened) continue;
    if (opened.challenge) return { outcome: 'blocked', why: `${short(opened.url)} “${opened.title}”` };
    const next = opened.isPdf ? [opened.url] : opened.pdfUrls.filter((u) => !tried.has(u));
    next.forEach((u) => tried.add(u));
    if (!next.length) notes.push(`opening ${short(link)}: “${opened.title}”`);
    for (const u of next) {
      const done = await attempt(u);
      if (done) return { outcome: done };
    }
  }
  return { outcome: unverified ? 'unverified' : wrong ? 'wrong-paper' : 'no-access', why: [...new Set(notes)].join(' · ') };
}

/**
 * The PDF at ``link``, fetched as the page in the tab would fetch it (its cookies, its origin),
 * then as this extension page (no CORS limits); otherwise why not (a login page, say).
 * @param {number} tabId
 * @param {string} link
 * @returns {Promise<Uint8Array<ArrayBuffer> | string>}
 */
async function fetchPdf(tabId, link) {
  const inPage = await inTab(tabId, fetchPdfInPage, [link, MAX_PDF_BYTES]);
  if (inPage?.b64) return fromBase64(inPage.b64);
  try {
    const r = await fetch(link, { credentials: 'include' });
    const buf = new Uint8Array(await r.arrayBuffer());
    if (r.ok && buf.length <= MAX_PDF_BYTES && isPdf(buf)) return buf;
  } catch { /* a network error, or the site wants its own page: opening it comes next */ }
  return inPage?.error || 'no answer';
}

/** A link as shown in the details: host and path, shortened. @param {string} url */
function short(url) {
  try {
    const u = new URL(url);
    const s = u.host.replace(/^www\./, '') + u.pathname;
    return s.length > 70 ? `${s.slice(0, 67)}…` : s;
  } catch {
    return url;
  }
}

/** @param {string} b64 */
const fromBase64 = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
/** @param {Uint8Array} buf */
const isPdf = (buf) => buf[0] === 0x25 && buf[1] === 0x50 && buf[2] === 0x44 && buf[3] === 0x46;

/**
 * Run one of extract.js's functions in the tab; ``undefined`` while the tab is between pages.
 * @template {any[]} A
 * @template R
 * @param {number} tabId
 * @param {(...args: A) => R | Promise<R>} func
 * @param {A} args
 * @returns {Promise<R | undefined>}
 */
async function inTab(tabId, func, args) {
  try {
    const [res] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
    return /** @type {R | undefined} */ (res?.result);
  } catch {
    return undefined;
  }
}

/**
 * Load ``url`` in the tab and wait until it has loaded (or LOAD_MS passed).
 * @param {number} tabId
 * @param {string} url
 */
function navigate(tabId, url) {
  return new Promise((resolve, reject) => {
    /** @param {number} id @param {chrome.tabs.OnUpdatedInfo} info */
    const onUpdated = (id, info) => { if (id === tabId && info.status === 'complete') { done(); resolve(undefined); } };
    /** @param {number} id */
    const onRemoved = (id) => { if (id === tabId) { done(); reject(new Error('The tab was closed.')); } };
    const timer = setTimeout(() => { done(); resolve(undefined); }, LOAD_MS);
    function done() {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      chrome.tabs.onRemoved.removeListener(onRemoved);
    }
    chrome.tabs.onUpdated.addListener(onUpdated);
    chrome.tabs.onRemoved.addListener(onRemoved);
    chrome.tabs.update(tabId, { url }).catch((e) => { done(); reject(e); });
  });
}

/**
 * Wait for the tab to show the article or a PDF: a browser check runs, then reloads the page by
 * itself, as do the redirect pages some publishers put first. The last state seen when the
 * page stops changing, or after SETTLE_MS.
 * @param {number} tabId
 * @returns {Promise<PageState | undefined>}
 */
async function settle(tabId) {
  const end = Date.now() + SETTLE_MS;
  /** @type {PageState | undefined} */
  let last;
  let steady = 0;
  let unreadable = 0;
  while (Date.now() < end) {
    const tab = await chrome.tabs.get(tabId);
    const state = tab.status === 'complete' ? await inTab(tabId, pageState, []) : undefined;
    // Chrome's built-in PDF viewer forbids script injection. Its original tab URL can still
    // be fetched by the extension; fetchPdf checks the bytes before anything is uploaded.
    unreadable = tab.status === 'complete' && !state ? unreadable + 1 : 0;
    if (unreadable >= 2 && tab.url && /^https?:/.test(tab.url)) {
      return { url: tab.url, title: tab.title || '', isPdf: true, challenge: false, pdfUrls: [] };
    }
    if (state && (state.isPdf || state.pdfUrls.length)) return state;
    steady = state && !state.challenge && state.url === last?.url ? steady + 1 : 0;
    if (state) last = state;
    // a few seconds without change: pages that build themselves by script add their links late
    if (steady >= STEADY_POLLS) return last;
    await sleep(POLL_MS);
  }
  return last;
}

window.addEventListener('beforeunload', (e) => { if (running) e.preventDefault(); });
init();
