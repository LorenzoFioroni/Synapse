// The browser's side of "Ask AI": which companions are connected, a question sent to one, and the
// answer followed as it streams (GET /api/ai/jobs/<id>?after=N is a long poll). The companion and
// the server are backend/companion; the contract is in docs/companion.md. Plain fetch, no api.ts:
// the standalone PDF viewer loads this too and does not share the app's modules.

/** An agent a companion offers. ``models`` and ``efforts`` may be empty (an older companion): no selector then. */
export interface AiProvider { id: string; label: string; models?: { id: string; label: string }[]; efforts?: string[] }
export interface AiCompanion { id: string; name: string; providers: AiProvider[]; busy: number }
export interface AiStatus { configured: boolean; companions: AiCompanion[] }

/** What a question is about: a note (and words selected in it), a paper (its PDF page, words selected there) or a PDF of the notes folders. */
export type AskContext =
  | { kind: 'note'; path: string; selection?: string }
  | { kind: 'paper'; item: string; attachment?: number; page?: number; quote?: string }
  | { kind: 'notepdf'; path: string; page?: number; quote?: string };   // a PDF kept in a notes folder

export interface AskOptions { context?: AskContext[]; conversation?: string; provider?: string; companion?: string; model?: string; effort?: string }
export interface AskReply { job: string; conversation: string; provider: string; companion: { id: string; name: string } }
export type JobState = 'queued' | 'running' | 'done' | 'error' | 'cancelled';
export interface Usage { input_tokens?: number; output_tokens?: number; cost_usd?: number; duration_ms?: number }
export type JobEvent = { type: 'text'; text: string } | { type: 'tool'; name: string } | { type: 'change'; tool: string } | { type: 'started' } | ({ type: 'usage' } & Usage)
  | { type: 'done' } | { type: 'error'; message: string };

/** A stored conversation (GET /api/ai/conversations). */
export interface Conversation {
  id: string; title: string; created: string; updated: string; provider: string; model?: string | null; effort?: string | null;
  companion: { id: string; name: string }; context?: AskContext | AskContext[] | null; available: boolean; running: string | null;
}
export interface StoredMessage { role: 'user' | 'assistant'; text: string; context?: AskContext[]; tools?: string[]; changes?: string[]; usage?: Usage; state?: string; error?: string; created?: string }

export class AiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

async function call<T>(method: 'GET' | 'POST' | 'PATCH' | 'DELETE', url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
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

export const ask = (prompt: string, o: AskOptions): Promise<AskReply> => call<AskReply>('POST', '/api/ai/ask', { prompt, ...o });

export const listConversations = (q = ''): Promise<{ conversations: Conversation[] }> =>
  call('GET', `/api/ai/conversations?limit=50${q ? `&q=${encodeURIComponent(q)}` : ''}`);
export const getConversation = (id: string): Promise<Conversation & { messages: StoredMessage[] }> => call('GET', `/api/ai/conversations/${encodeURIComponent(id)}`);
export const renameConversation = (id: string, title: string): Promise<unknown> => call('PATCH', `/api/ai/conversations/${encodeURIComponent(id)}`, { title });
export const deleteConversation = (id: string): Promise<unknown> => call('DELETE', `/api/ai/conversations/${encodeURIComponent(id)}`);

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
