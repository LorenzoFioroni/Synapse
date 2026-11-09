// Optional live receiver for requests from the library's other devices. An active long poll
// advertises availability; expired requests are never stored or replayed after reconnecting.
import { loadConnection, libraryFetch } from './connection.js';
import { libraryPdfRequest } from './bridge.js';

/** @type {AbortController | null} */
let current = null;

export function refreshPdfReceiver() {
  current?.abort();
  const controller = new AbortController();
  current = controller;
  void receive(controller);
}

/** @param {AbortController} controller */
async function receive(controller) {
  const client = crypto.randomUUID();
  /** @type {{server: string, token: string} | null} */
  let registered = null;
  try {
    while (!controller.signal.aborted) {
      try {
        const { server, token } = await loadConnection();
        const { pdfReceiver } = /** @type {{pdfReceiver?: {server: string, enabled: boolean}}} */ (await chrome.storage.local.get('pdfReceiver'));
        if (controller.signal.aborted || !pdfReceiver?.enabled || pdfReceiver.server !== server) return;
        if (!await chrome.permissions.contains({ origins: ['https://*/*'] })) return;

        /** @param {string} path @param {Record<string, unknown> | undefined} [body] @param {number} [timeout] */
        const api = async (path, body, timeout = 24000) => {
          const request = new AbortController();
          const abort = () => request.abort();
          controller.signal.addEventListener('abort', abort, { once: true });
          if (controller.signal.aborted) request.abort();
          const timer = setTimeout(abort, timeout);
          try {
            const response = await libraryFetch(server, token, path, {
              method: body ? 'POST' : 'GET', signal: request.signal,
              ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
            });
            if (!response.ok) throw new Error(`Library returned ${response.status}`);
            return await response.json();
          } finally {
            clearTimeout(timer);
            controller.signal.removeEventListener('abort', abort);
          }
        };

        const ping = await api('/api/connector/ping', undefined, 3000);
        if (!ping.capabilities?.live_pdf_requests) return;
        registered = { server, token };
        const { job } = await api(`/api/connector/browser-pdfs?client=${encodeURIComponent(client)}`);
        if (!job || controller.signal.aborted) continue;
        const ids = libraryPdfRequest({ type: 'find-publisher-pdfs', ids: job.ids }, { url: server, frameId: 0 }, server);
        if (!ids || typeof job.id !== 'string') continue;
        // Recheck at the server immediately before opening anything. A response delivered after
        // sleep, suspension or a slow connection must not revive a timed-out phone request.
        const started = performance.now();
        const claim = await api('/api/connector/browser-pdfs/claim', { client, id: job.id }, 3000);
        if (controller.signal.aborted || !(claim.remaining > 0)
          || performance.now() - started >= claim.remaining * 1000) continue;
        let opened;
        try {
          const query = new URLSearchParams({ ids: ids.join(','), auto: '1', fallback: '1', library: server });
          // Keep the window idle until the server accepts the handoff. The finder can start as
          // soon as it loads, so a late acknowledgement must not race the server's fallback.
          opened = await chrome.windows.create({
            url: 'about:blank', type: 'popup', width: 650, height: 760,
          });
          const tabId = opened?.tabs?.[0]?.id;
          if (tabId === undefined) throw new Error('Could not open the PDF finder');
          const ack = await api('/api/connector/browser-pdfs/ack', { client, id: job.id, accepted: true }, 3000);
          if (!ack.active) {
            if (opened?.id !== undefined) await chrome.windows.remove(opened.id);
            continue;
          }
          await chrome.tabs.update(tabId, { url: chrome.runtime.getURL(`find_pdfs.html?${query}`) });
        } catch {
          if (opened?.id !== undefined) await chrome.windows.remove(opened.id).catch(() => {});
          await api('/api/connector/browser-pdfs/ack', { client, id: job.id, accepted: false }, 3000).catch(() => {});
        }
      } catch {
        if (controller.signal.aborted) return;
        await new Promise((resolve) => {
          const timer = setTimeout(done, 2000);
          function done() { clearTimeout(timer); controller.signal.removeEventListener('abort', done); resolve(undefined); }
          controller.signal.addEventListener('abort', done, { once: true });
        });
      }
    }
  } finally {
    // Release the old instance promptly when settings change, rather than leaving a ghost
    // listener until its long poll times out. A crash still expires naturally at the server.
    if (registered) await libraryFetch(registered.server, registered.token, '/api/connector/browser-pdfs/leave', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client }),
      signal: AbortSignal.timeout(3000),
    }).catch(() => {});
  }
}
