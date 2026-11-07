// The bell says once per new version that Synapse can be updated (Settings → Updates). Every page
// asks the server, which checks GitHub itself every few hours (backend/online/updates.py).
import { api } from './api.js';
import { notify } from './notifications/center.js';
import { emit } from './store.js';
import { plural } from './util.js';

const SEEN = 'synapse.update-notified';   // the newest commit already announced on this device
const FIRST_MS = 20_000;
const EVERY_MS = 6 * 3600_000;

async function look(): Promise<void> {
  let st;
  try { st = await api.updates(); } catch { return; }   // offline, restarting, or no update checks
  const newest = st.latest?.commit;
  if (!st.available || !st.behind || !newest) return;
  try {
    if (localStorage.getItem(SEEN) === newest) return;
    localStorage.setItem(SEEN, newest);
  } catch { /* storage unavailable: announce it on this page */ }
  notify(`A Synapse update is available: ${plural(st.behind, 'new commit')} on ${st.source?.branch ?? 'its branch'}.`, {
    action: { label: 'Show', run: () => emit('open-settings', 'updates') },
  });
}

export function watchUpdates(): void {
  setTimeout(() => void look(), FIRST_MS);
  setInterval(() => void look(), EVERY_MS);
}
