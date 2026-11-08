/** A persistent, non-blocking status for work outside the page (browser prompts or server jobs).
 * Each operation owns its notice, so overlapping work cannot clear another operation's feedback. */
export function pendingStatus(message: string): { set(message: string): void; close(): void } {
  const notice = document.createElement('div');
  notice.className = 'toast progress-toast operation-status';
  notice.setAttribute('role', 'status');
  const spinner = document.createElement('span');
  spinner.className = 'spinner';
  spinner.setAttribute('aria-hidden', 'true');
  const text = document.createElement('span');
  text.className = 'msg';
  text.textContent = message;
  notice.append(spinner, text);
  document.getElementById('toasts')!.append(notice);
  return { set(message) { text.textContent = message; }, close() { notice.remove(); } };
}

export async function withPendingStatus<T>(message: string, action: () => Promise<T>): Promise<T> {
  const status = pendingStatus(message);
  try { return await action(); } finally { status.close(); }
}
