// Where the editor's ghost text (ghost.ts) gets its suggestions: the server, which asks a model
// in Ollama on this computer (backend/assist). Most computers have no Ollama, or not the model,
// so suggestions are only asked for once the server's status says both are there. Until then
// nothing is sent; one notice per page says what is missing, and the status is checked again
// from time to time (so starting Ollama later is noticed).
import { api } from '../../api.js';
import { state } from '../../store.js';
import { toast } from '../../ui/toast.js';
import type { AssistStatus } from '../../types.js';
import type { GhostSource } from './ghost.js';

const RECHECK_OK = 120_000;   // ms between status checks while all is well
const RECHECK_BAD = 30_000;   // ... and while something is missing

let status: AssistStatus | null = null;
let checkedAt = 0;
let checking: Promise<void> | null = null;
let noticeShown = false;
// Answers that mean asking again soon is pointless (a timeout may just be the model loading)
const LASTING = new Set(['unreachable', 'no_model', 'unsupported', 'error']);

const enabled = () => state.settings.notes_ai_complete === true;

/** Ask the server whether suggestions can be made (and load the model while the person reads). */
export function checkAssist({ force = false } = {}): Promise<void> {
  if (!enabled()) { status = null; return Promise.resolve(); }
  const stale = Date.now() - checkedAt > (status?.ok ? RECHECK_OK : RECHECK_BAD);
  if (checking || (!force && !stale)) return checking || Promise.resolve();
  checking = (async () => {
    try {
      status = await api.assistStatus({ warm: true, fresh: force });
    } catch {
      status = null;   // the server itself is gone: nothing to suggest
    } finally {
      checkedAt = Date.now();
      checking = null;
    }
    if (status && !status.ok && status.enabled) notice(status.message);
  })();
  return checking;
}

/** A status checked elsewhere (Settings' Check Again): the editor goes by it from now on. */
export function assistStatusKnown(st: AssistStatus): void {
  status = st;
  checkedAt = Date.now();
  if (st.ok) noticeShown = false;   // a later problem gets its notice again
}

/** Settings changed: check again if anything about autocompletion did. */
let lastKey = '';
export function assistSettingsChanged(): void {
  const s = state.settings;
  const key = `${s.notes_ai_complete}|${s.notes_ai_url}|${s.notes_ai_model}`;
  if (key === lastKey) return;
  lastKey = key;
  noticeShown = false;
  status = null;
  checkedAt = 0;
  if (enabled()) checkAssist({ force: true });
}

function notice(message: string): void {
  if (noticeShown) return;
  noticeShown = true;
  toast(`Autocompletion is on, but it cannot make suggestions: ${message}`, {
    timeout: 9000,
    action: { label: 'Settings', run: async () => (await import('../settingsView.js')).openSettings('assist') },
  });
}

/** The ghost-text source for the notes editor. */
export const assistSource: GhostSource = {
  ready() {
    if (!enabled()) return false;
    checkAssist();   // in the background when stale; this keystroke goes by the last answer
    return !!status?.ok;
  },
  async complete(prefix, suffix, signal) {
    const r = await api.assistComplete(prefix, suffix, signal);
    if (r.reason && LASTING.has(r.reason) && status) {
      // Ollama stopped or the model went away: stop asking until the next status check
      status = { ...status, ok: false, reason: r.reason, message: r.message || status.message };
      checkedAt = Date.now();
      notice(r.message || 'the model did not answer.');
    }
    return r.text || '';
  },
};
