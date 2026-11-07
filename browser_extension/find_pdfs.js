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
/** @typedef {{ id: string; title: string; doi: string; year?: number | null }} Missing */
/** @typedef {'attached' | 'wrong-paper' | 'has-pdf' | 'no-access' | 'no-link' | 'blocked' | 'error'} Outcome */

const params = new URLSearchParams(location.search);
// The automated tests point the DOIs at a local page instead of doi.org.
const RESOLVER = params.get('resolver') || 'https://doi.org/';
const PAUSE_MS = Number(params.get('pause') ?? 5000);   // between papers
const LOAD_MS = 45000;      // one navigation
const SETTLE_MS = 25000;    // a browser check, redirects by script
const POLL_MS = 1000;
const MAX_PDF_BYTES = 60 * 1024 * 1024;
const BATCH = 50;

/** @type {Record<Outcome, [string, 'ok' | 'warn' | 'err']>} */
const OUTCOMES = {
  attached: ['PDF attached ✓', 'ok'],
  'has-pdf': ['Already has a PDF', 'ok'],
  'wrong-paper': ['A PDF came back, but not of this paper', 'warn'],
  'no-access': ['No access: the PDF link gave a login page', 'warn'],
  'no-link': ['No PDF link on the article page', 'warn'],
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
    const ping = await api('/api/connector/ping');
    if (!ping.ok) throw new Error(ping.data?.error || `Error ${ping.status}`);
    folders = ping.data.folders || [];
  } catch (e) {
    conn.className = 'conn off';
    view.innerHTML = `<div class="result"><div class="big err">!</div><h2>Library unavailable</h2>
      <p>${esc(e instanceof Error ? e.message : e)}</p><p>Check the extension’s settings, then reopen this window.</p></div>`;
    return;
  }
  conn.className = 'conn ok';
  conn.title = `Connected to ${server}`;
  renderForm();
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

async function renderForm(folder = '') {
  view.innerHTML = `
    <p class="intro">Opens each paper’s DOI in a background tab of this browser and attaches the publisher’s PDF,
      using the access this browser has (campus network, VPN, your sign-ins). Keep this window open while it runs.</p>
    <label class="f" for="folder">Items without a PDF in</label>
    <select id="folder">${folderOptions(folder)}</select>
    <div id="list"><div class="center"><span class="spinner"></span> Loading…</div></div>`;
  get('folder', HTMLSelectElement).onchange = (e) => {
    if (e.target instanceof HTMLSelectElement) renderForm(e.target.value);
  };
  const q = new URLSearchParams({ limit: String(BATCH), ...(folder ? { folder_id: folder } : {}) });
  const res = await api(`/api/connector/missing-pdfs?${q}`);
  const list = get('list', HTMLElement);
  if (!res.ok) {
    list.innerHTML = `<p class="summary">${esc(res.data?.error || `Error ${res.status}`)}</p>`;
    return;
  }
  items = res.data.items;
  outcomes.clear();
  if (!items.length) {
    list.innerHTML = '<p class="summary">Every item with a DOI here has a PDF.</p>';
    return;
  }
  const more = res.data.total > items.length ? ` (the ${items.length} newest of ${res.data.total}; run again for the rest)` : '';
  list.innerHTML = `
    <p class="summary">${items.length} item${items.length === 1 ? '' : 's'} with a DOI and no PDF${esc(more)}.</p>
    <div class="actions"><button class="btn primary" id="start">Find PDFs</button><button class="btn" id="stop" disabled>Stop</button></div>
    <div class="hint">One paper every few seconds. If a site asks you to confirm you are human, do it in its tab.</div>
    <ul class="rows">${items.map((it) => `<li data-id="${esc(it.id)}"><span class="what"><b>${esc(it.title || it.doi)}</b>
      <small>${esc(it.doi)}${it.year ? ` · ${esc(it.year)}` : ''}</small></span><span class="st">Waiting</span></li>`).join('')}</ul>`;
  get('start', HTMLButtonElement).onclick = () => start(items);
  get('stop', HTMLButtonElement).onclick = () => { stopping = true; get('stop', HTMLButtonElement).disabled = true; };
}

/**
 * @param {string} id
 * @param {string} text
 * @param {'ok' | 'warn' | 'err' | 'busy' | ''} [kind]
 */
function setStatus(id, text, kind = '') {
  const row = [...document.querySelectorAll('.rows li')].find((li) => li instanceof HTMLElement && li.dataset.id === id);
  const st = row?.querySelector('.st');
  if (st) { st.className = `st ${kind}`; st.innerHTML = kind === 'busy' ? `<span class="spinner"></span> ${esc(text)}` : esc(text); }
}

/** @param {Missing[]} todo */
async function start(todo) {
  // Pages of any publisher: asked for here, on the click, since Chrome grants it only then.
  const local = RESOLVER.startsWith('http://127.0.0.1');   // the tests' pages, already allowed
  if (!local && !(await chrome.permissions.request({ origins: ['https://*/*'] }).catch(() => false))) {
    get('list', HTMLElement).insertAdjacentHTML('afterbegin', '<p class="summary">The extension needs access to publishers’ pages to read the PDF links.</p>');
    return;
  }
  running = true;
  stopping = false;
  const startBtn = get('start', HTMLButtonElement);
  startBtn.disabled = true;
  get('stop', HTMLButtonElement).disabled = false;
  get('folder', HTMLSelectElement).disabled = true;
  /** @type {number | undefined} */
  let tabId;
  try {
    for (const [n, it] of todo.entries()) {
      if (stopping) break;
      if (n) await sleep(PAUSE_MS);
      if (stopping) break;
      tabId = await workerTab(tabId);
      /** @type {Outcome} */
      let outcome;
      try {
        outcome = await findOne(tabId, it);
      } catch (e) {
        console.warn('Find PDF failed for', it.doi, e);
        outcome = 'error';
      }
      outcomes.set(it.id, outcome);
      const [text, kind] = OUTCOMES[outcome];
      setStatus(it.id, text, kind);
    }
  } finally {
    if (tabId !== undefined) await chrome.tabs.remove(tabId).catch(() => {});
    running = false;
    finish();
  }
}

function finish() {
  const done = [...outcomes.values()];
  const attached = done.filter((o) => o === 'attached').length;
  const failed = items.filter((it) => outcomes.has(it.id) && !['attached', 'has-pdf'].includes(String(outcomes.get(it.id))));
  const startBtn = get('start', HTMLButtonElement);
  get('stop', HTMLButtonElement).disabled = true;
  get('folder', HTMLSelectElement).disabled = false;
  startBtn.disabled = !failed.length;
  startBtn.textContent = failed.length ? `Retry ${failed.length} not found` : 'Done';
  startBtn.onclick = () => start(failed);
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
 * @returns {Promise<Outcome>}
 */
async function findOne(tabId, it) {
  setStatus(it.id, 'Opening the article…', 'busy');
  await navigate(tabId, RESOLVER + it.doi.split('/').map(encodeURIComponent).join('/'));
  const page = await settle(tabId);
  if (!page) return 'error';
  if (page.challenge) return 'blocked';
  const links = page.isPdf ? [page.url] : page.pdfUrls;
  if (!links.length) return 'no-link';
  setStatus(it.id, 'Getting the PDF…', 'busy');
  let wrong = false;
  // From the article page first; opening a link in the tab leaves that page, so that comes last.
  for (const open of [false, true]) {
    for (const link of links) {
      const pdf = await fetchPdf(tabId, link, open);
      if (pdf === 'blocked') return 'blocked';
      if (!pdf) continue;
      setStatus(it.id, 'Checking it is the paper…', 'busy');
      const fd = new FormData();
      fd.append('id', it.id);
      fd.append('file', new Blob([pdf], { type: 'application/pdf' }), 'paper.pdf');
      const res = await api('/api/connector/attach-pdf', { method: 'POST', body: fd });
      if (!res.ok) throw new Error(res.data?.error || `Error ${res.status}`);
      if (res.data.status === 'attached' || res.data.status === 'has-pdf') return res.data.status;
      wrong ||= res.data.status === 'wrong-paper';
    }
  }
  return wrong ? 'wrong-paper' : 'no-access';
}

/**
 * The PDF at ``link``. Without ``open``: fetched as the article page would fetch it (its cookies,
 * its origin), then as this extension page (no CORS limits). With ``open``: opened in the tab, for
 * PDFs behind a redirect by script (ScienceDirect), and fetched from there. ``null`` when what
 * comes back is not a PDF (a login page, say).
 * @param {number} tabId
 * @param {string} link
 * @param {boolean} open
 * @returns {Promise<Uint8Array<ArrayBuffer> | null | 'blocked'>}
 */
async function fetchPdf(tabId, link, open) {
  if (!open) {
    const inPage = await inTab(tabId, fetchPdfInPage, [link, MAX_PDF_BYTES]);
    if (inPage?.b64) return fromBase64(inPage.b64);
    try {
      const r = await fetch(link, { credentials: 'include' });
      const buf = new Uint8Array(await r.arrayBuffer());
      if (r.ok && buf.length <= MAX_PDF_BYTES && isPdf(buf)) return buf;
    } catch { /* a network error, or the site wants its own page: opening it comes next */ }
    return null;
  }
  await navigate(tabId, link);
  const page = await settle(tabId);
  if (page?.challenge) return 'blocked';
  if (!page?.isPdf) return null;
  const opened = await inTab(tabId, fetchPdfInPage, [page.url, MAX_PDF_BYTES]);
  return opened?.b64 ? fromBase64(opened.b64) : null;
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
  while (Date.now() < end) {
    const tab = await chrome.tabs.get(tabId);
    const state = tab.status === 'complete' ? await inTab(tabId, pageState, []) : undefined;
    if (state && (state.isPdf || state.pdfUrls.length)) return state;
    steady = state && !state.challenge && state.url === last?.url ? steady + 1 : 0;
    if (state) last = state;
    if (steady >= 2) return last;
    await sleep(POLL_MS);
  }
  return last;
}

window.addEventListener('beforeunload', (e) => { if (running) e.preventDefault(); });
init();
