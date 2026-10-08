import { icon } from '../icons.js';
import { escapeHtml, h } from '../util.js';

let root;

/**
 * Show a transient notification.
 * toast('Saved') · toast('Failed', {type: 'error'}) · toast('Moved to Trash', {action: {label: 'Undo', run}})
 */
export function toast(message, { type = 'info', action = null, timeout = 4200 } = {}) {
  root ||= document.getElementById('toasts');
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
  el.querySelector('.toast-close').onclick = close;
  if (action) el.querySelector('.toast-action').onclick = () => { close(); action.run(); };
  root.appendChild(el);
  while (root.children.length > 4) root.firstElementChild.remove();
  if (timeout) setTimeout(close, type === 'error' ? timeout * 1.6 : timeout);
  return close;
}

export const toastError = (e) => toast(e?.message || String(e), { type: 'error' });
