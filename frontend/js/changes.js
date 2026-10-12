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

let lastTyped = 0;
document.addEventListener('keydown', () => { lastTyped = Date.now(); }, true);
document.addEventListener('input', () => { lastTyped = Date.now(); }, true);

/** A refresh now would get in the way: a dialog or an autocomplete list is open, a field is being
    edited (fields save when left, so a refresh would lose what is typed), or the note editor was
    typed in during the last few seconds (it saves itself, hash-checked). It waits for the next poll. */
function editing() {
  if (document.querySelector('.dialog-backdrop, .cm-tooltip-autocomplete')) return true;
  const a = document.activeElement;
  if (!a || a === document.body || a.id === 'search-input') return false;
  if (a.closest?.('.cm-editor')) return Date.now() - lastTyped < 4000;
  return a.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(a.tagName);
}

async function apply() {
  if (editing()) return;   // tried again at the next poll
  if (pending.has('notes')) {
    pending.delete('notes');
    notesChanged();
  }
  if (pending.has('library')) {
    if (Date.now() - lastLibrary < MIN_GAP) return;   // tried again at the next poll
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
