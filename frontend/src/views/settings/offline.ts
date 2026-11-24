// Settings → Offline Use: the switch that keeps the app, the library and the PDFs on this device
// (frontend/src/offline.ts, offlineSync.ts; docs/offline.md), and how much of it is saved.
import { OFFLINE_EVENT, changesWaiting, offlineEnabled, offlineSupported, offlineNow } from '../../offline.js';
import { SYNC_EVENT, disableOffline, enableOffline, lastSynced, syncOffline, syncState } from '../../offlineSync.js';
import { confirmDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import { formatBytes, formatDate, plural } from '../../util.js';

async function storageText(): Promise<string> {
  try {
    const { usage, quota } = await navigator.storage.estimate();
    return usage ? `${formatBytes(usage)} used${quota ? ` of about ${formatBytes(quota)} this browser allows` : ''}.` : '';
  } catch { return ''; }
}

/** Fill the section and keep it current while the Settings page is open; returns the function that stops that. */
export function bindOffline(view: HTMLElement): () => void {
  const q = <T extends HTMLElement>(sel: string) => view.querySelector<T>(sel)!;
  const sw = q<HTMLInputElement>('[data-offline-switch]');
  const desc = q('[data-offline-desc]');
  const status = q('[data-offline-status]');
  const title = q('[data-offline-title]');
  const detail = q('[data-offline-detail]');
  const bar = q('[data-offline-progress]');
  const sync = q<HTMLButtonElement>('[data-offline-sync]');
  let busy = false;

  const paint = async (): Promise<void> => {
    const on = offlineEnabled();
    sw.checked = on;
    sw.disabled = busy || !offlineSupported();
    desc.textContent = offlineSupported()
      ? 'Saves the app, your library and every PDF in this browser, and keeps them up to date. The files stay on this device and use its disk space.'
      : 'This browser cannot keep Synapse offline here: it needs https (or Synapse open as localhost).';
    status.hidden = !on;
    if (!on) return;
    const s = syncState;
    sync.disabled = s.running || !navigator.onLine;
    bar.hidden = !s.running || !s.total;
    if (s.running) {
      title.textContent = `Saving ${s.step.toLowerCase()}…`;
      detail.textContent = s.total ? `${s.done} of ${s.total}${s.failed ? ` · ${s.failed} could not be saved` : ''}` : 'Reading the library…';
      (bar.firstElementChild as HTMLElement).style.width = `${s.total ? Math.round((100 * s.done) / s.total) : 0}%`;
      return;
    }
    const last = lastSynced();
    const waiting = changesWaiting();
    if (!last) { title.textContent = 'Not saved yet'; detail.textContent = navigator.onLine ? 'Save Now keeps the library on this device.' : 'Go online to save the library.'; return; }
    title.textContent = last.pdfs ? `${last.saved} of ${plural(last.pdfs, 'PDF')} saved` : 'App and library saved';
    const parts = [`Last saved ${formatDate(new Date(last.at).toISOString(), true)}`];
    if (last.failed) parts.push(`${plural(last.failed, 'file')} could not be saved: they are tried again next time`);
    if (waiting) parts.push(`${plural(waiting, 'annotation change')} waiting to be sent`);
    if (offlineNow()) parts.push('offline now');
    const used = await storageText();
    if (used) parts.push(used);
    detail.textContent = parts.join(' · ');
  };

  sw.addEventListener('change', async () => {
    const turnOn = sw.checked;
    if (!turnOn) {
      const waiting = changesWaiting();
      if (!(await confirmDialog('Turn off offline use on this device?', {
        confirm: 'Turn Off', danger: !!waiting,
        detail: 'The saved copy of the app, the library and the PDFs is deleted from this browser. Synapse needs a connection again.'
          + (waiting ? ` ${plural(waiting, 'annotation change')} made offline have not been sent yet and will be lost.` : ''),
      }))) { void paint(); return; }
      busy = true; void paint();
      await disableOffline();
      busy = false; void paint();
      return;
    }
    busy = true; void paint();
    try {
      await enableOffline();
      toast('Saved for offline use', { type: 'success' });
    } catch (e) { toastError(e); }
    busy = false; void paint();
  });
  sync.addEventListener('click', () => { syncOffline(true).catch((e) => { if (offlineEnabled()) toastError(e); }); });

  const events = [OFFLINE_EVENT, SYNC_EVENT];
  const listener = () => { void paint(); };
  for (const ev of events) window.addEventListener(ev, listener);
  void paint();
  return () => { for (const ev of events) window.removeEventListener(ev, listener); };
}
