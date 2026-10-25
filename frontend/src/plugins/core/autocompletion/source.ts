// Where the editor's ghost text (views/notes/ghost.ts) gets its suggestions: the plugin's server
// part, which asks a model in Ollama on this computer. Most computers have no Ollama, or not the
// model, so suggestions are only asked for once the status says both are there. Until then
// nothing is sent; one notice per page says what is missing, and the status is checked again from
// time to time (so starting Ollama later is noticed).
import { api } from '../../../api.js';
import type { PluginContext } from '../../api.js';
import type { GhostSource } from '../../../views/notes/ghost.js';

/** Why suggestions can or cannot be made (the plugin's /status). */
export type AssistReason = '' | 'off' | 'unreachable' | 'no_model' | 'unsupported' | 'bad_url' | 'timeout' | 'error' | 'superseded';
export interface AssistStatus {
  enabled: boolean;
  ok: boolean;
  reason: AssistReason;
  message: string;
  url: string;
  model: string;
  models: string[];      // installed in Ollama
  version: string;       // Ollama's
  fim: boolean;          // the model fills in the middle (sees the text after the cursor)
  thinking: boolean;
}
interface AssistCompletion { text: string; reason: AssistReason; message?: string }

const RECHECK_OK = 120_000;   // ms between status checks while all is well
const RECHECK_BAD = 30_000;   // ... and while something is missing
// Answers that mean asking again soon is pointless (a timeout may just be the model loading)
const LASTING = new Set(['unreachable', 'no_model', 'unsupported', 'bad_url', 'error']);

export interface Source {
  ghost: GhostSource;
  /** Ask whether suggestions can be made (and load the model while the person reads). */
  check(opts?: { force?: boolean }): Promise<void>;
  /** A status checked elsewhere (Settings' Check Again): the editor goes by it from now on. */
  known(st: AssistStatus): void;
  /** The settings changed: forget what was known. */
  reset(): void;
}

export function makeSource(p: PluginContext): Source {
  let status: AssistStatus | null = null;
  let checkedAt = 0;
  let checking: Promise<void> | null = null;
  let noticeShown = false;

  function notice(message: string): void {
    if (noticeShown) return;
    noticeShown = true;
    p.notify(`it is on, but it cannot make suggestions: ${message}`, {
      timeout: 9000,
      action: { label: 'Settings', run: async () => (await import('../../../views/settingsView.js')).openSettings('x-autocompletion-ollama') },
    });
  }

  function check({ force = false } = {}): Promise<void> {
    const stale = Date.now() - checkedAt > (status?.ok ? RECHECK_OK : RECHECK_BAD);
    if (checking || (!force && !stale)) return checking || Promise.resolve();
    checking = (async () => {
      try {
        status = await p.backend.get<AssistStatus>('/status', { warm: 1, fresh: force ? 1 : undefined });
      } catch {
        status = null;   // the server itself is gone: nothing to suggest
      } finally {
        checkedAt = Date.now();
        checking = null;
      }
      if (status && !status.ok) notice(status.message);
    })();
    return checking;
  }

  return {
    check,
    known(st) {
      status = st;
      checkedAt = Date.now();
      if (st.ok) noticeShown = false;   // a later problem gets its notice again
    },
    reset() {
      noticeShown = false;
      status = null;
      checkedAt = 0;
      check({ force: true });
    },
    ghost: {
      ready() {
        check();   // in the background when stale; this keystroke goes by the last answer
        return !!status?.ok;
      },
      async complete(prefix, suffix, signal) {
        // the app's own request, so a suggestion overtaken by typing is cancelled
        const r = await api.pluginRoute<AssistCompletion>(p.id, 'POST', '/complete', { prefix, suffix }, undefined, signal);
        if (r.reason && LASTING.has(r.reason) && status) {
          // Ollama stopped or the model went away: stop asking until the next status check
          status = { ...status, ok: false, reason: r.reason, message: r.message || status.message };
          checkedAt = Date.now();
          notice(r.message || 'the model did not answer.');
        }
        return r.text || '';
      },
    },
  };
}
