import { startJob, type NoticeOptions } from '../notifications/center.js';
import { icon } from '../icons.js';
import { escapeHtml, h } from '../util.js';

let root: HTMLElement;

export type ToastType = 'info' | 'success' | 'error';
export interface ToastAction { label: string; run: () => void }

/**
 * Show a transient notification.
 * toast('Saved') · toast('Failed', {type: 'error'}) · toast('Moved to Trash', {action: {label: 'Undo', run}})
 */
export function toast(message: string, { type = 'info', action = null, timeout = 4200 }: { type?: ToastType; action?: ToastAction | null; timeout?: number } = {}): () => void {
  root ||= document.getElementById('toasts')!;
  const ico = { info: 'info', success: 'check', error: 'alert' }[type] || 'info';
  const el = h(`<div class="toast ${type}" role="status">
      ${icon(ico)}<span class="msg">${escapeHtml(message)}</span>
      ${action ? `<button class="toast-action">${escapeHtml(action.label)}</button>` : ''}
      <button class="toast-close" aria-label="Dismiss">${icon('close', 'icon-sm')}</button>
    </div>`);
  const close = () => {
    if (!el.isConnected) return;
    el.classList.add('leaving');
    setTimeout(() => el.remove(), 180);
  };
  el.querySelector<HTMLElement>('.toast-close')!.onclick = close;
  if (action) el.querySelector<HTMLElement>('.toast-action')!.onclick = () => { close(); action.run(); };
  root.appendChild(el);
  const plain = () => root.querySelectorAll('.toast:not(.progress-toast)');
  while (plain().length > 4) plain()[0].remove();
  if (timeout) setTimeout(close, type === 'error' ? timeout * 1.6 : timeout);
  return close;
}

export const toastError = (e: unknown) => toast((e as Error | null)?.message || String(e), { type: 'error' });

/**
 * A notification-center job: progress bar, live text
 * and an optional Cancel button. finish() retains its result in history.
 */
export interface ProgressToast {
  set(text: string, done?: number, total?: number): void;
  close(): void;
  finish(message?: string, options?: NoticeOptions): void;
}

export function progressToast(text: string, { onCancel = null }: { onCancel?: (() => void) | null } = {}): ProgressToast {
  return startJob(text, onCancel);
}
