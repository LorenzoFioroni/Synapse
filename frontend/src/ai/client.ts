// The browser's side of "Ask AI": which companions are connected, a question sent to one, and the
// answer followed as it streams (GET /api/ai/jobs/<id>?after=N is a long poll). The companion and
// the server are backend/companion; the contract is in docs/companion.md. Plain fetch, no api.ts:
// the standalone PDF viewer loads this too and does not share the app's modules.

export interface AiProvider { id: string; label: string }
export interface AiCompanion { id: string; name: string; providers: AiProvider[]; busy: number }
export interface AiStatus { configured: boolean; companions: AiCompanion[] }

/** Where the question was asked: a note (and the words selected in it) or a paper's PDF page. */
export type AskContext =
  | { kind: 'note'; path: string; selection?: string }
  | { kind: 'paper'; item: string; attachment?: number; page?: number; quote?: string };

export interface AskReply { job: string; conversation: string; provider: string; companion: { id: string; name: string } }
export type JobState = 'queued' | 'running' | 'done' | 'error' | 'cancelled';
export type JobEvent = { type: 'text'; text: string } | { type: 'tool'; name: string } | { type: 'done' } | { type: 'error'; message: string };

export class AiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

async function call<T>(method: 'GET' | 'POST', url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { method, signal, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    if (signal?.aborted) throw e;
    throw new AiError('Cannot reach the server. Is it still running?', 0);
  }
  let data: { error?: string } | null = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new AiError(data?.error || `Request failed (${res.status})`, res.status);
  return data as T;
}

export const MAX_PROMPT = 20000;
export const MAX_CONTEXT = 8000;

export const fetchStatus = (): Promise<AiStatus> => call<AiStatus>('GET', '/api/ai/status');

export function ask(prompt: string, o: { context?: AskContext; conversation?: string; provider?: string }): Promise<AskReply> {
  return call<AskReply>('POST', '/api/ai/ask', { prompt, context: o.context, conversation: o.conversation, provider: o.provider });
}

export const cancelJob = (job: string): Promise<{ state: JobState }> => call('POST', `/api/ai/jobs/${encodeURIComponent(job)}/cancel`);

const FINAL: JobState[] = ['done', 'error', 'cancelled'];
export const isFinal = (s: JobState): boolean => FINAL.includes(s);
const sleep = (ms: number, signal: AbortSignal) => new Promise<void>((resolve) => {
  const t = setTimeout(resolve, ms);
  signal.addEventListener('abort', () => { clearTimeout(t); resolve(); }, { once: true });
});

/**
 * Follow a job until it ends, calling ``onEvents`` with each batch. A dropped connection is retried
 * a few times (the job goes on at the server); aborting ``signal`` stops quietly. Resolves to the
 * final state, or rejects with the error that stopped the following.
 */
export async function follow(job: string, onEvents: (events: JobEvent[], state: JobState) => void, signal: AbortSignal): Promise<JobState> {
  let after = 0, failures = 0;
  while (!signal.aborted) {
    try {
      const r = await call<{ events: JobEvent[]; next: number; state: JobState }>('GET', `/api/ai/jobs/${encodeURIComponent(job)}?after=${after}`, undefined, signal);
      failures = 0;
      after = r.next;
      onEvents(r.events, r.state);
      if (isFinal(r.state)) return r.state;
    } catch (e) {
      if (signal.aborted) break;
      if (e instanceof AiError && e.status === 404) throw new AiError('This answer is no longer available. Ask again.', 404);
      if (++failures > 5) throw e;
      await sleep(Math.min(8000, 500 * 2 ** failures), signal);
    }
  }
  return 'cancelled';
}
