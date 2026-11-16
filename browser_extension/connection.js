// The extension never talks to the Synapse server itself: every library request goes through
// the local Synapse Companion program over Chrome native messaging, which holds the connection
// and its credentials. One long-lived port per extension context (page or worker), opened on
// first use and reopened after it closes.
const HOST = 'org.synapse.companion';
const NOT_INSTALLED = 'Synapse Companion is not installed on this computer, or not set up for this browser. Open Synapse Companion and connect it to your library.';
const DEFAULT_TIMEOUT = 30;   // seconds, as the host's default
const GRACE_MS = 5000;        // the host answers a timeout itself; this only covers a silent host

/** @typedef {{ id: string, type: string, status?: number, body?: string, more?: boolean, message?: string, server?: string, name?: string }} Reply */
/** @typedef {{ resolve: (reply: Reply) => void, reject: (error: Error) => void, parts: string[], timer: ReturnType<typeof setTimeout> | undefined }} Pending */

/** @type {chrome.runtime.Port | null} */
let port = null;
/** @type {Map<string, Pending>} */
const pending = new Map();

/** @param {Pending} entry */
const settle = (entry) => clearTimeout(entry.timer);

/** @param {Error} error */
function failAll(error) {
  for (const entry of pending.values()) { settle(entry); entry.reject(error); }
  pending.clear();
}

function connect() {
  if (port) return port;
  const opened = chrome.runtime.connectNative(HOST);
  opened.onMessage.addListener((/** @type {Reply} */ message) => {
    const entry = message && pending.get(message.id);
    if (!entry) return;   // aborted, timed out or unknown: ignored
    if (message.type === 'error') {
      pending.delete(message.id); settle(entry);
      entry.reject(new Error(message.message || 'Synapse Companion reported an error.'));
    } else if (message.type === 'response') {
      entry.parts.push(message.body || '');
      if (!message.more) {
        pending.delete(message.id); settle(entry);
        entry.resolve({ ...message, body: entry.parts.join('') });
      }
    } else if (message.type === 'hello') {
      pending.delete(message.id); settle(entry);
      entry.resolve(message);
    }
  });
  opened.onDisconnect.addListener(() => {
    const reason = chrome.runtime.lastError?.message;
    if (port === opened) port = null;
    failAll(new Error(reason || NOT_INSTALLED));
  });
  port = opened;
  return opened;
}

/**
 * @param {Record<string, unknown>} message  without id
 * @param {{ signal?: AbortSignal | null, timeout?: number }} [opts]
 * @returns {Promise<Reply>}
 */
function send(message, { signal, timeout = DEFAULT_TIMEOUT } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('The request was aborted.', 'AbortError')); return; }
    const id = crypto.randomUUID();
    /** @type {Pending} */
    const entry = { resolve, reject, parts: [], timer: undefined };
    const drop = () => { pending.delete(id); settle(entry); };
    signal?.addEventListener('abort', () => {
      if (!pending.has(id)) return;
      drop();
      reject(new DOMException('The request was aborted.', 'AbortError'));
    }, { once: true });
    entry.timer = setTimeout(() => {
      if (!pending.has(id)) return;
      drop();
      reject(new Error('Synapse Companion did not answer in time.'));
    }, timeout * 1000 + GRACE_MS);
    pending.set(id, entry);
    try {
      connect().postMessage({ id, ...message });
    } catch (error) {
      drop();
      reject(error instanceof Error ? error : new Error(NOT_INSTALLED));
    }
  });
}

/** The library this computer's companion is connected to. @returns {Promise<{ server: string, name: string }>} */
export async function companionHello() {
  const build = await fetch(chrome.runtime.getURL('build.json'), { cache: 'no-store' })
    .then((res) => (res.ok ? res.json() : null)).then((data) => (typeof data?.build === 'string' ? data.build : null))
    .catch(() => null);
  const reply = await send({ type: 'hello', version: chrome.runtime.getManifest().version, build }, { timeout: 10 });
  if (typeof reply.server !== 'string' || !reply.server) throw new Error('Synapse Companion is not connected to a library.');
  return { server: reply.server, name: reply.name || '' };
}

/** @param {ArrayBuffer} buffer */
function base64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/** @param {FormData} form */
async function formEntries(form) {
  /** @type {Array<[string, string | { data: string, filename: string, type: string }]>} */
  const entries = [];
  for (const [name, value] of form.entries()) {
    if (typeof value === 'string') entries.push([name, value]);
    else entries.push([name, { data: base64(await value.arrayBuffer()), filename: value.name || 'blob', type: value.type }]);
  }
  return entries;
}

/**
 * A request to the library through the companion, answered like fetch() (an HTTP error status
 * resolves; an unreachable companion or server rejects). `path` starts with /api/connector/.
 * @param {string} path
 * @param {RequestInit & { timeout?: number }} [opts]  `timeout` in seconds (host maximum 60)
 * @returns {Promise<Response>}
 */
export async function libraryFetch(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  /** @type {Record<string, unknown>} */
  const message = { type: 'request', method, path };
  if (opts.timeout) message.timeout = opts.timeout;
  const body = opts.body;
  if (body instanceof FormData) message.form = await formEntries(body);
  else if (typeof body === 'string') message.json = JSON.parse(body);
  else if (body != null) throw new TypeError('Unsupported request body.');
  const reply = await send(message, { signal: opts.signal, timeout: opts.timeout });
  const status = reply.status || 0;
  // A Response cannot carry a body for these statuses or a status outside 200-599.
  const empty = [204, 205, 304].includes(status);
  return new Response(empty ? null : reply.body, { status: status >= 200 && status <= 599 ? status : 502 });
}
