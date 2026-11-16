// Whether Ask AI is set up (a companion token exists), shared by the entry points. They stay
// hidden until the server says so; the answer is cached briefly and read synchronously.
import { type AiStatus, fetchStatus } from './client.js';

let status: AiStatus | null = null;
let fetchedAt = 0;
const listeners = new Set<() => void>();

export const aiStatus = (): AiStatus | null => status;
export const aiConfigured = (): boolean => !!status?.configured;
export const onAiStatus = (fn: () => void): void => { listeners.add(fn); };

/** Ask the server again unless the answer is younger than ``maxAge`` ms. Never throws. */
export async function refreshAiStatus(maxAge = 0): Promise<AiStatus | null> {
  if (status && Date.now() - fetchedAt < maxAge) return status;
  try {
    const was = status?.configured;
    status = await fetchStatus();
    fetchedAt = Date.now();
    if (was !== status.configured) listeners.forEach((fn) => fn());
  } catch { /* an old server without /api/ai: the entry points stay hidden */ }
  return status;
}
