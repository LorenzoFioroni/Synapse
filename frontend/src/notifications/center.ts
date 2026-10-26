import { icon } from '../icons.js';
import { emit, on, state } from '../store.js';
import { escapeHtml, formatDate, h } from '../util.js';
import { badge, faviconBadge, showDesktop } from './desktop.js';
import { History, PREFIX, newId, type Level, type Notice } from './model.js';
import { monitorOwners } from './ownership.js';
export interface NoticeAction { label: string; run: () => unknown }
export interface NoticeOptions {
  type?: Level; desktop?: boolean; source?: string; target?: string; action?: NoticeAction | null;
}
const memory = new Map<string, Notice>();
const actions = new Map<string, NoticeAction>();
const cancels = new Map<string, () => void>();
let panel: HTMLElement | null = null;
let bell: HTMLButtonElement | null = null;
const owner = newId();
const history = new History({
  read: () => {
    const all = new Map(memory);
    try { for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)!;
      if (key.startsWith(PREFIX)) { try { const n = JSON.parse(localStorage.getItem(key)!); all.set(key.slice(PREFIX.length), n); } catch { /* corrupt entry */ } }
    } } catch { /* storage unavailable: retain this page's history */ }
    return [...all.values()];
  },
  write: n => { memory.set(n.id, n); try { localStorage.setItem(PREFIX + n.id, JSON.stringify(n)); } catch { /* memory fallback */ } },
  remove: id => { memory.delete(id); actions.delete(id); cancels.delete(id); try { localStorage.removeItem(PREFIX + id); } catch { /* memory fallback */ } },
}, render);
let ownerTracking = false;

export function notify(message: string, options: NoticeOptions = {}): string {
  const n: Notice = { id: newId(), message, level: options.type || 'info', status: 'done', time: Date.now(), read: false, source: options.source, target: options.target, desktop: options.desktop };
  if (options.action) actions.set(n.id, options.action);
  history.put(n); showDesktop(n, openCenter);
  return n.id;
}
export function releaseSource(source: string): void {
  for (const n of history.entries) if (n.source === source) actions.delete(n.id);
  render();
}
export interface Job {
  set(message: string, done?: number, total?: number): void;
  finish(message?: string, options?: NoticeOptions): void;
  close(): void;
}
export function startJob(message: string, onCancel?: (() => void) | null): Job {
  let n: Notice = { id: newId(), message, level: 'info', status: 'running', owner, time: Date.now(), read: false };
  let finished = false;
  if (onCancel) cancels.set(n.id, () => { cancels.delete(n.id); onCancel(); render(); });
  history.put(n);
  const finish = (text = n.message, options: NoticeOptions = {}) => {
    if (finished) return; finished = true; cancels.delete(n.id);
    n = { ...n, message: text, status: 'done', level: options.type || 'success', read: false, time: Date.now(), target: options.target, source: options.source, desktop: options.desktop };
    if (options.action) actions.set(n.id, options.action);
    history.put(n); showDesktop(n, openCenter);
  };
  return { set(text, done = 0, total = 0) { if (!finished) { n = { ...n, message: text, done, total, read: history.entries.find(x => x.id === n.id)?.read ?? n.read }; history.put(n); } }, finish, close: () => finish() };
}
function closeCenter(): void {
  if (!panel) return;
  panel.hidden = true; bell?.setAttribute('aria-expanded', 'false'); bell?.focus();
}
export function openCenter(): void {
  if (!panel) return;
  panel.style.top = `${(bell?.getBoundingClientRect().bottom || 48) + 8}px`;
  panel.hidden = false; bell?.setAttribute('aria-expanded', 'true');
  panel.querySelector<HTMLButtonElement>('[data-n="close"]')?.focus();
}
export function initNotifications(button: HTMLButtonElement): void {
  ownerTracking = monitorOwners(owner, history, () => { ownerTracking = false; render(); });
  bell = button;
  panel = h(`<section id="notification-center" role="dialog" aria-labelledby="notification-heading" hidden>
    <header><h2 id="notification-heading">Notifications</h2><button class="icon-btn" data-n="close" aria-label="Close notifications">${icon('close')}</button></header>
    <div class="notification-tools"><button data-n="read">Mark all read</button><button data-n="clear">Clear completed</button></div>
    <div class="notification-list" role="list"></div></section>`);
  document.body.append(panel);
  bell.onclick = () => { if (panel!.hidden) openCenter(); else closeCenter(); };
  panel.onclick = e => {
    const b = (e.target as Element).closest<HTMLButtonElement>('button'); if (!b) return;
    const id = b.closest<HTMLElement>('[data-notice]')?.dataset.notice;
    if (b.dataset.n === 'close') closeCenter();
    if (b.dataset.n === 'read') history.markRead();
    if (b.dataset.n === 'clear') history.clear();
    if (!id) return;
    if (b.dataset.n === 'dismiss') {
      const n = history.entries.find(n => n.id === id);
      history.remove(id, true, !ownerTracking && n?.owner !== owner);
    }
    if (b.dataset.n === 'seen') history.markRead(id);
    if (b.dataset.n === 'cancel') cancels.get(id)?.();
    if (b.dataset.n === 'action') {
      const n = history.entries.find(x => x.id === id); history.markRead(id);
      if (n?.target) { if (state.sources.some(s => `source:${s.id}` === n.target)) { emit('open-view', n.target); closeCenter(); } }
      else { const action = actions.get(id); if (action?.label === 'Undo') actions.delete(id); render(); closeCenter();
        try { Promise.resolve(action?.run()).catch(e => notify(String(e), { type: 'error' })); } catch (e) { notify(String(e), { type: 'error' }); }
      }
    }
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !panel!.hidden) { e.preventDefault(); e.stopImmediatePropagation(); closeCenter(); } }, true);
  document.addEventListener('pointerdown', e => { if (!panel!.hidden && !panel!.contains(e.target as Node) && !bell!.contains(e.target as Node)) { panel!.hidden = true; bell!.setAttribute('aria-expanded', 'false'); } });
  window.addEventListener('storage', e => { if (!e.key || e.key.startsWith(PREFIX)) { if (e.key) memory.delete(e.key.slice(PREFIX.length)); else memory.clear(); history.reload(); } });
  on('sources', render);
  window.addEventListener('pagehide', () => history.interrupt(owner));
  render();
}
function render(): void {
  if (!bell || !panel) return;
  const count = history.unread;
  bell.setAttribute('aria-label', `Notifications${count ? `, ${count} unread` : ''}`);
  bell.querySelector<HTMLElement>('.notification-badge')!.textContent = count > 99 ? '99+' : String(count);
  bell.querySelector<HTMLElement>('.notification-badge')!.hidden = !count;
  badge(count); faviconBadge(count);
  panel.querySelector('[data-n="read"]')!.toggleAttribute('disabled', !count);
  const list = panel.querySelector('.notification-list')!;
  const focused = document.activeElement as HTMLElement | null;
  const focusId = focused?.closest<HTMLElement>('[data-notice]')?.dataset.notice;
  const focusAction = focused?.dataset.n;
  list.innerHTML = history.entries.length ? history.entries.map(n => {
    const running = n.status === 'running';
    const targetAvailable = !!n.target && state.sources.some(s => `source:${s.id}` === n.target);
    const pct = n.total && n.total > 0 ? Math.max(0, Math.min(100, (n.done || 0) / n.total * 100)) : null;
    return `<article role="listitem" data-notice="${escapeHtml(n.id)}" class="notification ${n.level}${n.read ? '' : ' unread'}">
      <div class="notification-symbol">${running ? '<span class="spinner"></span>' : icon(n.level === 'success' ? 'check' : ['error', 'warning'].includes(n.level) ? 'alert' : 'info')}</div>
      <div class="notification-body"><div class="notification-meta">${escapeHtml(n.level)} · ${escapeHtml(running ? 'Running' : n.status === 'done' ? formatDate(new Date(n.time).toISOString(), true) : n.status)}</div>
      <div class="notification-message">${escapeHtml(n.message)}</div>
      ${running ? `<progress aria-label="Job progress" ${pct === null ? '' : `value="${pct}" max="100"`}></progress>` : ''}
      <div class="notification-actions">${targetAvailable || (!n.target && actions.has(n.id)) ? `<button data-n="action">${escapeHtml(n.target ? 'Show' : actions.get(n.id)!.label)}</button>` : ''}
      ${cancels.has(n.id) ? '<button data-n="cancel">Cancel</button>' : ''}${!n.read ? '<button data-n="seen">Mark read</button>' : ''}
      ${!running || (!ownerTracking && n.owner !== owner) ? '<button data-n="dismiss">Dismiss</button>' : ''}</div></div></article>`;
  }).join('') : '<p class="notification-empty">No notifications yet.</p>';
  if (focusId && focusAction) {
    const next = [...list.querySelectorAll<HTMLButtonElement>('button')].find(b => b.dataset.n === focusAction && b.closest<HTMLElement>('[data-notice]')?.dataset.notice === focusId);
    (next || panel.querySelector<HTMLButtonElement>('[data-n="close"]'))?.focus();
  }
}
