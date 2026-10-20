// Tells the server that a Synapse window is open. A server started by the desktop shortcut
// (python server.py --window --quit-when-closed) stops once no window is left, like a program
// whose window was closed; any other server answers that it does not care, and the page stops
// asking. Used by the app (main.ts) and by a viewer in a browser tab of its own (viewer.ts); a
// viewer inside the app's tabs is counted with the app around it.

const ID = Math.random().toString(36).slice(2);
const EVERY = 30_000;
let watching = false;
let timer: ReturnType<typeof setTimeout> | undefined;

function send(path: 'ping' | 'bye', keepalive = false): Promise<Response> {
  return fetch(`/api/window/${path}`, {
    method: 'POST', keepalive, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client: ID }),
  });
}

async function ping(): Promise<void> {
  clearTimeout(timer);
  try {
    const r = await send('ping');
    watching = r.ok && !!(await r.json()).watch;
  } catch {
    watching = true;   // the server is busy or restarting: keep saying hello
  }
  if (watching) timer = setTimeout(ping, EVERY);
}

export function startPresence(): void {
  if (window.top !== window) return;
  ping();
  // Closing the window (or leaving the page) says goodbye at once; a reload says hello again
  // before the server gives up waiting.
  window.addEventListener('pagehide', () => { if (watching) send('bye', true).catch(() => {}); });
  window.addEventListener('pageshow', (e) => { if (e.persisted) ping(); });
}
