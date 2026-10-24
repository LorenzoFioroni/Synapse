/** Optional browser capabilities must never prevent a notice from reaching the center. */
import type { Notice } from './model.js';
const KEY = 'synapse.notifications.desktop';
export function desktopStatus(): string {
  if (!('Notification' in window) || !window.isSecureContext) return 'OS notifications unavailable';
  if (Notification.permission === 'denied') return 'OS notifications blocked in browser settings';
  return enabled() ? 'Disable OS notifications' : 'Enable OS notifications';
}
function enabled(): boolean { try { return localStorage.getItem(KEY) === 'on'; } catch { return false; } }
export async function toggleDesktop(): Promise<void> {
  if (!('Notification' in window) || !window.isSecureContext) return;
  try {
    if (enabled()) localStorage.removeItem(KEY);
    else if (await Notification.requestPermission() === 'granted') localStorage.setItem(KEY, 'on');
  } catch { /* Center remains available when permission or storage is unavailable. */ }
}
export function showDesktop(n: Notice, open: () => void): void {
  if (!n.desktop || n.status === 'running' || !enabled() || !('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    const notice = new Notification('Synapse', { body: n.message, tag: n.id, icon: '/assets/favicon-32.png' });
    notice.onclick = () => { window.focus(); open(); notice.close(); };
  } catch { /* Some mobile browsers expose the API but require a service worker. */ }
}
export function badge(count: number): void {
  const nav = navigator as Navigator & { setAppBadge?: (n: number) => Promise<void>; clearAppBadge?: () => Promise<void> };
  try { void (count ? nav.setAppBadge?.(count) : nav.clearAppBadge?.())?.catch(() => {}); } catch { /* unsupported platform */ }
}

let lastCount = -1;
let original: { el: HTMLLinkElement; href: string }[] | null = null;
let favicon: HTMLImageElement | null = null;
/** Keep a visible count on ordinary browser tabs as well as supported installed-app icons. */
export function faviconBadge(count: number): void {
  if (count === lastCount) return;
  const image = favicon ||= new Image();
  lastCount = count;
  original ||= [...document.querySelectorAll<HTMLLinkElement>('link[rel="icon"], link[rel="shortcut icon"]')].map(el => ({ el, href: el.href }));
  if (!count) { for (const { el, href } of original) el.href = href; return; }
  const draw = () => {
    if (lastCount !== count) return;
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 32;
    const ctx = canvas.getContext('2d'); if (!ctx) return;
    ctx.drawImage(image, 0, 0, 32, 32);
    ctx.fillStyle = '#d93a3a'; ctx.beginPath(); ctx.arc(23, 9, 9, 0, 2 * Math.PI); ctx.fill();
    ctx.fillStyle = '#fff'; ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(count > 99 ? '99+' : String(count), 23, 9);
    for (const { el } of original!) el.href = canvas.toDataURL('image/png');
  };
  if (image.complete && image.naturalWidth) draw();
  else { image.onload = draw; image.src = '/assets/favicon-32.png'; }
}
