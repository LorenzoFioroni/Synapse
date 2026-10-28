// Small plugin-owned DOM and preference helpers; keys retain existing Graph preferences.
import type { PluginContext } from '../../../frontend/src/plugins/api.js';
let plugin: PluginContext;
export function configureUI(p: PluginContext): void { plugin = p; }
export const icon = (name: string, cls = '') => plugin.ui.icon(name, cls);
export const escapeHtml = (text: unknown) => String(text ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
export const toast = (message: string, options: { type?: 'info' | 'success' | 'error' } = {}) => plugin.notify(message, { ...options, transient: true });
export function toastError(error: unknown): void { if ((error as Error)?.name !== 'AbortError') toast(String(error), { type: 'error' }); }
export function h<T extends HTMLElement = HTMLElement>(html: string): T {
  const template = document.createElement('template'); template.innerHTML = html.trim(); return template.content.firstElementChild as T;
}
export function storage<T>(key: string, fallback: T): T { try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; } catch { return fallback; } }
export function store(key: string, value: unknown): void { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Preferences remain usable in memory. */ } }
export function shortAuthors(authors: string): string {
  const names = authors.split(';').map(a => a.split(',')[0].trim()).filter(Boolean);
  return names.length < 2 ? names[0] || '' : names.length === 2 ? names.join(' and ') : `${names[0]} et al.`;
}
