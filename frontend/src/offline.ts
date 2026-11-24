// Offline support, page side: registers the service worker (frontend/sw.js), asks it to send
// queued annotation changes when the connection comes back, and shows a small pill while
// offline or while changes wait. See docs/offline.md.

type OutboxMsg = { type: 'outbox'; waiting: number; sent?: number; dropped?: number };
type KeepMsg = { type: 'keep-progress'; done: number; failed: number; total: number };

declare global {
  interface Window { synapseOffline?: { keep(urls: string[]): void; flush(): void } }
}

const inFrame = window.top !== window;   // the viewer runs in an iframe: the main page shows the pill
let waiting = 0;
let pill: HTMLElement | null = null;

function show(text: string | null): void {
  if (inFrame) return;
  if (!text) { pill?.remove(); pill = null; return; }
  if (!pill) {
    pill = document.createElement('div');
    pill.className = 'zc-offline-pill';
    pill.style.cssText = 'position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:99999;'
      + 'padding:5px 12px;border-radius:999px;font:12px/1.4 system-ui,sans-serif;'
      + 'background:#3a3a3c;color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.25);pointer-events:none';
    document.body.appendChild(pill);
  }
  pill.textContent = text;
}

function refresh(): void {
  const off = !navigator.onLine;
  const w = waiting ? ` · ${waiting} annotation change${waiting === 1 ? '' : 's'} waiting` : '';
  if (off) show(`Offline — saved papers still open${w}`);
  else if (waiting) show(`Sending${w}…`);
  else show(null);
}

function post(msg: unknown): void { navigator.serviceWorker?.controller?.postMessage(msg); }

if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((e) => console.warn('[offline] service worker:', e));
  navigator.serviceWorker.addEventListener('message', (e: MessageEvent<OutboxMsg | KeepMsg>) => {
    const m = e.data;
    if (m?.type === 'outbox') {
      waiting = m.waiting;
      refresh();
      if (m.sent && !inFrame) console.info(`[offline] sent ${m.sent} queued change(s)`);
      if (m.dropped && !inFrame) show(`${m.dropped} queued change(s) were refused by the server`);
    } else if (m?.type === 'keep-progress' && !inFrame) {
      show(m.done + m.failed < m.total ? `Saving for offline: ${m.done}/${m.total}`
        : `Saved for offline: ${m.done}${m.failed ? ` (${m.failed} failed)` : ''}`);
      if (m.done + m.failed >= m.total) setTimeout(refresh, 4000);
    }
  });
  navigator.serviceWorker.ready.then(() => post('status'));
  window.addEventListener('online', () => { refresh(); post('flush'); });
  window.addEventListener('offline', refresh);
  window.synapseOffline = { keep: (urls) => post({ keep: urls }), flush: () => post('flush') };
  refresh();
}

export {};
