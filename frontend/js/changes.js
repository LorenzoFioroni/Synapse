// Changes made elsewhere — in another window, on the iPad, by background work or by the MCP server —
// show up here without a reload (issue #34). The page asks /api/changes every few seconds while it is
// visible (and at once when it becomes visible again); the server leaves out this page's own edits.
import * as A from './actions.js';
import { api } from './api.js';
import { notesChanged } from './views/notes/notesView.js';

const EVERY = 4000;          // ms between polls while the page is visible
const MIN_GAP = 2500;        // at most one library refresh this often (background fetching can be busy)

let seq = null;
let epoch = null;
let timer = null;
let busy = false;
let pending = new Set();
let lastLibrary = 0;

/** The person is typing or a dialog is open: a refresh now would get in the way. */
function editing() {
  const a = document.activeElement;
  if (document.querySelector('.dialog-backdrop')) return true;
  if (!a || a === document.body) return false;
  if (a.id === 'search-input') return false;
  return !!a.closest?.('#item-pane, #items-pane') && (a.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(a.tagName));
}

async function apply() {
  if (pending.has('notes')) {
    pending.delete('notes');
    notesChanged();
  }
  if (pending.has('library')) {
    if (editing() || Date.now() - lastLibrary < MIN_GAP) return;   // tried again at the next poll
    pending.delete('library');
    lastLibrary = Date.now();
    await A.loadItems();
  }
}

async function poll() {
  if (busy || document.hidden) return;
  busy = true;
  try {
    const res = await api.changes(seq ?? undefined, epoch ?? undefined);
    seq = res.seq;
    epoch = res.epoch;
    for (const k of res.changes || []) pending.add(k);
    pending.delete('annotations');   // the PDF viewer looks after its own
    if (pending.size) await apply();
  } catch { /* server away for a moment: try again next time */ }
  busy = false;
}

export function initChanges() {
  if (timer) return;
  poll();
  timer = setInterval(poll, EVERY);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  window.zcChanges = { poll, state: () => ({ seq, epoch, pending: [...pending] }) };   // for tests
}
