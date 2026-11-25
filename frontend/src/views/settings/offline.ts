// Settings → Offline Use: the switch that keeps the app, the library and the PDFs on this device
// (frontend/src/offline.ts, offlineSync.ts; docs/offline.md), and how much of it is saved.
import { OFFLINE_EVENT, changesWaiting, offlineEnabled, offlineSupported, offlineNow, savedPdfCount } from '../../offline.js';
import { NOTES_EVENT, loadNotes, localNotes, notePending, placeOps } from '../../offlineNotes.js';
import { loadQueue, queuedCount } from '../../plugins/offlineQueue.js';
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
      ? 'Saves the app and your library in this browser and keeps them up to date. PDFs are not downloaded: you choose each one.'
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
    const pdfs = savedPdfCount();
    const notes = localNotes().length;
    const kept = [pdfs && plural(pdfs, 'PDF'), notes && plural(notes, 'note')].filter(Boolean).join(' and ');
    title.textContent = `App and library saved · ${kept ? `${kept} available offline` : 'no PDFs or notes available offline yet'}`;
    const edited = localNotes().filter((n) => notePending(n.path)).length;
    const parts = [`Last saved ${formatDate(new Date(last.at).toISOString(), true)}`];
    if (last.failed) parts.push(`${plural(last.failed, 'file')} could not be saved: they are tried again next time`);
    if (waiting) parts.push(`${plural(waiting, 'annotation change')} waiting to be sent`);
    if (edited) parts.push(`${plural(edited, 'note')} changed offline, not yet in Synapse`);
    if (placeOps().length) parts.push(`${plural(placeOps().length, 'rename, move or deletion', 'renames, moves or deletions')} of notes, PDFs or folders not yet in Synapse`);
    if (queuedCount()) parts.push(`${plural(queuedCount(), 'plugin change')} (tasks, …) waiting to be sent`);
    if (offlineNow()) parts.push('offline now');
    const used = await storageText();
    if (used) parts.push(used);
    detail.textContent = parts.join(' · ');
  };

  sw.addEventListener('change', async () => {
    const turnOn = sw.checked;
    if (!turnOn) {
      const waiting = changesWaiting();
      const edited = localNotes().filter((n) => notePending(n.path)).length + placeOps().length;
      if (!(await confirmDialog('Turn off offline use on this device?', {
        confirm: 'Turn Off', danger: !!(waiting || edited),
        detail: 'The saved copy of the app, the library, the PDFs and the notes is deleted from this browser. Synapse needs a connection again.'
          + (waiting ? ` ${plural(waiting, 'annotation change')} made offline have not been sent yet and will be lost.` : '')
          + (edited ? ` ${plural(edited, 'offline change')} to notes, PDFs or folders (edits, renames, deletions) are not in Synapse yet and will be lost.` : '')
          + (queuedCount() ? ` ${plural(queuedCount(), 'change')} to tasks or other plugins' data will be lost.` : ''),
      }))) { void paint(); return; }
      busy = true; void paint();
      await disableOffline();
      busy = false; void paint();
      return;
    }
    busy = true; void paint();
    try {
      await enableOffline();
      toast('Offline use is on. Right-click a paper → Make Available Offline to keep its PDF.', { type: 'success', timeout: 6000 });
    } catch (e) { toastError(e); }
    busy = false; void paint();
  });
  sync.addEventListener('click', () => { syncOffline(true).catch((e) => { if (offlineEnabled()) toastError(e); }); });

  const events = [OFFLINE_EVENT, SYNC_EVENT, NOTES_EVENT];
  const listener = () => { void paint(); };
  void loadNotes();
  void loadQueue().then(listener);
  for (const ev of events) window.addEventListener(ev, listener);
  void paint();
  return () => { for (const ev of events) window.removeEventListener(ev, listener); };
}
