// Saving the library for offline use (the main page only; offline.ts is shared with the viewer).
// Turning offline use on saves the app's files and the library in the browser; the app then repeats
// it quietly when it starts, so a new version and new papers are saved too. PDFs are saved only one
// by one, when the user makes them available offline (offlineMenuEntry). See docs/offline.md.
import { api, fileUrl } from './api.js';
import { OFFLINE_EVENT, forgetFiles, offlineEnabled, offlineNow, offlineSupported, pdfSaved, pruneFiles, refreshSaved, removeOfflineCopy, saveFiles, setOfflineFlag, startOffline } from './offline.js';
import { PLACES_EVENT, type LocalNote, chosen, chosenPaths, dropNote, folderChosen, setChosen, loadNotes, localNote, localNotes, notePending, noteChosen, putNote, rekeyNote, removeLocalNotes, serverPath, setNotesPusher, unreachable } from './offlineNotes.js';
import { flushQueue, loadQueue, queuedCount, removeQueue, setBeforeFlush } from './plugins/offlineQueue.js';
import { emit } from './store.js';
import type { Attachment, Item } from './types.js';
import type { MenuEntry } from './ui/menu.js';
import { toast } from './ui/toast.js';
import { plural, storage, store } from './util.js';

export const SYNC_EVENT = 'zc:offline-sync';
const VERSION_KEY = 'zc.offline.shell';   // the app version that was saved
const DONE_KEY = 'zc.offline.synced';     // when everything was last saved
const CHUNK = 8;                          // files per request to the worker: it may stop between requests

export interface SyncState {
  running: boolean;
  /** What is being saved now: "App files" or "Library". */
  step: string;
  done: number;
  total: number;
  failed: number;
}

export const syncState: SyncState = { running: false, step: '', done: 0, total: 0, failed: 0 };

/** The result of the last complete save, kept across reloads: when, and how many files failed. */
export interface SyncResult { at: number; failed: number }
export const lastSynced = (): SyncResult | null => storage<SyncResult | null>(DONE_KEY, null);

const tick = () => window.dispatchEvent(new Event(SYNC_EVENT));

/** Saves ``urls`` a few at a time, counting them into ``syncState``. */
async function saveAll(step: string, urls: string[], refresh = false): Promise<number> {
  Object.assign(syncState, { step, done: 0, total: urls.length });
  tick();
  let failed = 0;
  for (let i = 0; i < urls.length; i += CHUNK) {
    if (!offlineEnabled()) throw new Error('Offline use was turned off.');
    const r = await saveFiles(urls.slice(i, i + CHUNK), refresh);
    failed += r.failed;
    syncState.done = Math.min(urls.length, i + CHUNK);
    syncState.failed += r.failed;
    tick();
  }
  return failed;
}

/** The data the app asked for while starting (settings, folders, tags, the plugins and their
 *  frontends' files…): it is saved again so it is there offline. */
function startupData(): string[] {
  return [...new Set(performance.getEntriesByType('resource')
    .map((e) => [new URL(e.name, location.href), (e as PerformanceResourceTiming).initiatorType] as const)
    .filter(([u, how]) => u.origin === location.origin && (u.pathname.startsWith('/api/plugin-files/')
      || (how === 'fetch' && u.pathname.startsWith('/api/') && !u.pathname.startsWith('/api/items/'))))
    .map(([u]) => u.pathname + u.search))];
}

let running: Promise<void> | null = null;

/** Saves what is missing (or, with ``all``, everything again). Calls while one runs share it. */
export function syncOffline(all = false): Promise<void> {
  if (!offlineEnabled() || !navigator.onLine) return Promise.resolve();
  running ||= run(all).finally(() => { running = null; syncState.running = false; tick(); });
  return running;
}

async function run(all: boolean): Promise<void> {
  Object.assign(syncState, { running: true, failed: 0 });
  tick();
  // 1. the app itself
  const shell = await fetch('/api/offline/shell', { cache: 'no-store' }).then((r) => r.json() as Promise<{ version: string; files: string[] }>);
  const stale = all || storage<string>(VERSION_KEY, '') !== shell.version;
  const appFailed = await saveAll('App files', shell.files, stale);
  if (!appFailed && offlineEnabled()) store(VERSION_KEY, shell.version);
  // 2. the library, and what the app needs to show it
  Object.assign(syncState, { step: 'Library', done: 0, total: 0 });
  tick();
  await pushNotes();
  await flushPlugins();
  const [library, notes, plugins] = await Promise.all([api.listItems({ view: 'library' }), api.notes().catch(() => null), api.plugins().catch(() => null)]);
  // the routes plugins keep for offline use (API 1.16)
  const kept = (plugins?.plugins || []).filter((p) => p.running && p.offline?.keep.length)
    .flatMap((p) => p.offline!.keep.map((k) => `/api/plugins/${encodeURIComponent(p.id)}${k}`));
  await saveFiles(kept, true);
  if (notes) await keepNotes(chosenPaths(notes));
  await saveFiles(startupData(), true);
  const items = library.items.filter((it) => !it._outside);
  await saveAll('Library', items.map((it) => `/api/items/${it.id}`));
  // 3. the PDFs made available offline: their annotations again (they may have changed elsewhere);
  //    the saved PDFs that left the library are forgotten
  await refreshSaved();
  const pdfs = [...pdfsOf(items), ...(notes?.pdfs || []).map((p) => ({ id: 0, url: `/api/note-pdfs/${p.id}/file`, type: 'pdf' }))];
  await saveFiles(pdfs.filter((a) => pdfSaved(fileUrl(a))).map(annotationsUrl), true);
  await pruneFiles(pdfs.flatMap((a) => [fileUrl(a), annotationsUrl(a)]));
  await refreshSaved();
  if (offlineEnabled()) store(DONE_KEY, { at: Date.now(), failed: syncState.failed });
}

/* ------------------------------------------------------------------ plugins */
let pluginNames: Record<string, string> = {};
/** Sends the plugins' writes made offline (plugins/offlineQueue.ts). */
export async function flushPlugins(): Promise<void> {
  await loadQueue();
  if (!queuedCount()) return;
  try { pluginNames = Object.fromEntries((await api.plugins()).plugins.map((p) => [p.id, p.name])); } catch { /* ids then */ }
  await flushQueue((id) => pluginNames[id] || id);
}

/* ------------------------------------------------------------------ notes */
let pushing: Promise<void> | null = null;

/** Sends the notes' offline edits to Synapse; a conflict waits for the user (resolveNote). */
export function pushNotes(): Promise<void> {
  pushing ||= doPush().finally(() => { pushing = null; });
  return pushing;
}

async function doPush(): Promise<void> {
  await loadNotes();
  // first what changes the notes' places, so the edits go to where the notes are now
  const places = localNotes().some((n) => n.trashed || n.movedFrom);
  const done = (await pushDeletions()) && (await pushMoves());
  if (places) window.dispatchEvent(new Event(PLACES_EVENT));
  if (!done) return;
  let conflicts = 0;
  for (const n of localNotes()) {
    if (!n.versions.length || n.conflict || n.trashed || n.movedFrom) continue;
    const sent = n.versions.length;
    let r;
    try { r = await api.applyOfflineNote(n.path, n.baseHash, n.baseText, n.versions); } catch (e) {
      if (unreachable(e)) return;   // still away: try again later
      toast(`Your offline edits to “${n.path}” could not be brought in: ${(e as Error).message}`, { type: 'error', timeout: 8000 });
      continue;
    }
    const now = localNote(n.path) || n;   // saves may have come in meanwhile
    if (r.status === 'conflict' || r.status === 'deleted') {
      conflicts++;
      await putNote({ ...now, conflict: { status: r.status, hash: r.hash, current: r.current, base: r.base, mine: r.mine, merged: r.merged } });
      continue;
    }
    const rest = now.versions.slice(sent);
    const last = n.versions[sent - 1].text;
    await putNote({
      ...now, versions: rest, conflict: null, file: { ...(now.file || {}), info: (r as { info?: NoteLocalInfo }).info ?? now.file?.info ?? null },
      // edits made while these went in are merged next time against what was sent (or taken as they are when it went in unchanged)
      baseHash: rest.length && r.status === 'merged' ? `merged:${r.hash}` : r.hash!,
      baseText: rest.length ? last : r.text!,
      text: rest.length ? now.text : r.text!,
      synced: now.localHash ? { local: now.localHash, hash: r.hash! } : undefined,
    });
  }
  if (conflicts) {
    toast(conflicts === 1 ? 'A note edited offline was also changed in Synapse: open it to choose.' : `${conflicts} notes edited offline were also changed in Synapse: open them to choose.`,
      { type: 'error', timeout: 10000, action: { label: 'Open', run: () => { const c = localNotes().find((x) => x.conflict); if (c) emit('open-note', { path: c.path }); } } });
  }
}
type NoteLocalInfo = NonNullable<LocalNote['file']>['info'];

const status = (e: unknown) => (e as { status?: number } | null)?.status;
const stem = (p: string) => p.replace(/^.*\//, '').replace(/\.md$/i, '');

/** Notes deleted offline go to the notes trash, unless they were changed in Synapse meanwhile.
 *  False while Synapse still cannot be reached. */
async function pushDeletions(): Promise<boolean> {
  for (const n of localNotes().filter((x) => x.trashed)) {
    if (n.baseHash === null) { await dropNote(n.path); continue; }   // created and deleted offline
    const at = serverPath(n);
    try {
      if (!(await api.offlineNoteOps.unchanged(at, n.baseHash.replace(/^merged:/, '')))) {
        await putNote({ ...n, trashed: undefined, trashId: undefined });
        toast(`“${stem(n.path)}” was changed in Synapse while you were offline, so it was not deleted.`, { timeout: 9000 });
        continue;
      }
      await api.offlineNoteOps.trash(at);
    } catch (e) {
      if (unreachable(e)) return false;
      if (status(e) !== 404) { toast(`“${stem(n.path)}” could not be deleted: ${(e as Error).message}`, { type: 'error', timeout: 8000 }); continue; }
    }
    await dropNote(n.path);
    setChosen([n.path], [], false);
  }
  return true;
}

/** Notes renamed or moved offline are renamed or moved in Synapse (which updates links to them).
 *  Tried until none goes in any more, so a chain of renames (b to c, then a to b) finds its order.
 *  False while Synapse still cannot be reached. */
async function pushMoves(): Promise<boolean> {
  const failed = new Map<string, unknown>();
  for (let progress = true; progress;) {
    progress = false;
    for (const n of localNotes().filter((x) => x.movedFrom && !x.trashed).sort((a, b) => (a.movedAt || 0) - (b.movedAt || 0))) {
      const from = n.movedFrom!;
      try {
        const r = await api.offlineNoteOps.move(from, n.path);
        const now = localNotes().find((x) => x.movedFrom === from);   // renamed again meanwhile: that goes next time
        if (now) await putNote({ ...now, movedFrom: now.path === r.path ? undefined : r.path });
        failed.delete(from);
        progress = true;
      } catch (e) {
        if (unreachable(e)) return false;
        failed.set(from, e);
      }
    }
  }
  for (const [from, e] of failed) {
    const n = localNotes().find((x) => x.movedFrom === from);
    if (!n) continue;
    if (status(e) === 404) {
      // deleted in Synapse meanwhile: edits made offline ask (apply_offline says "deleted"); nothing else is lost
      if (n.versions.length) await putNote({ ...n, movedFrom: undefined });
      else { await dropNote(n.path); toast(`“${stem(n.path)}” was deleted in Synapse while you were offline.`, { timeout: 9000 }); }
      continue;
    }
    toast(`“${stem(from)}” could not be renamed to “${stem(n.path)}”: ${(e as Error).message} It keeps its name.`, { type: 'error', timeout: 10000 });
    if (localNote(from)) await putNote({ ...n, movedFrom: undefined });
    else { await rekeyNote(n.path, { ...n, path: from, movedFrom: undefined }); setChosen([n.path], [], false); setChosen([from], [], true); }
  }
  return true;
}

/** Keeps the chosen notes' latest text here (api.getNote remembers it); forgets the ones no longer chosen. */
async function keepNotes(paths: string[]): Promise<void> {
  await loadNotes();
  Object.assign(syncState, { step: 'Notes', done: 0, total: paths.length });
  tick();
  for (let i = 0; i < paths.length; i++) {
    if (!offlineEnabled()) return;
    if (!notePending(paths[i])) { try { await api.getNote(paths[i]); } catch { /* locked or gone: not kept */ } }
    syncState.done = i + 1;
    if (i % 8 === 7) tick();
  }
  const keep = new Set(paths);
  for (const n of localNotes()) if (!keep.has(n.path) && !notePending(n.path) && !noteChosen(n.path)) await dropNote(n.path);
}

/** The user's choice for a note whose offline edits conflicted (or which was deleted meanwhile).
 *  ``text`` is what goes into the editor: the merge with conflict markers, the offline version or
 *  Synapse's; afterwards the note saves as usual, based on Synapse's version. */
export async function resolveNote(path: string, choice: 'merge' | 'mine' | 'theirs'): Promise<{ text: string; hash: string | null; deleted: boolean }> {
  const n = localNote(path);
  const c = n?.conflict;
  if (!n || !c) throw new Error('There is nothing to resolve for this note any more.');
  const deleted = c.status === 'deleted';
  const theirs = c.current ?? '';
  const text = deleted ? c.mine : choice === 'merge' ? (c.merged ?? c.mine) : choice === 'mine' ? c.mine : theirs;
  const hash = deleted ? null : c.hash ?? null;
  if (deleted && choice === 'theirs') await dropNote(path);
  else await putNote({ ...n, conflict: null, versions: [], baseHash: hash, baseText: deleted ? null : theirs, text: deleted ? text : theirs, localHash: undefined, synced: undefined });
  return { text, hash, deleted };
}

/** The notes list's menu entry that keeps these notes and folders for offline use, or stops; none while offline use is off. */
export function notesOfflineEntry(notes: string[], folders: string[]): MenuEntry | null {
  if (!offlineEnabled() || (!notes.length && !folders.length)) return null;
  const all = notes.every(noteChosen) && folders.every(folderChosen);
  if (!all) {
    return { label: 'Make Available Offline', icon: 'download', run: async () => {
      setChosen(notes, folders, true);
      let paths = notes;
      if (folders.length) { try { paths = [...notes, ...chosenPaths(await api.notes()).filter((p) => folders.some((f) => p.startsWith(`${f}/`)))]; } catch { /* saved at the next save */ } }
      await keepNotes([...new Set([...paths, ...localNotes().map((n) => n.path)])]);
      toast(`${paths.length === 1 ? 'The note is' : `${plural(paths.length, 'note')} are`} available offline`, { type: 'success' });
    } };
  }
  return { label: 'Remove Offline Copy', icon: 'download', run: async () => {
    setChosen(notes, folders, false);
    const still = notes.filter(noteChosen);
    for (const n of localNotes()) {
      if (noteChosen(n.path) || notePending(n.path)) continue;
      if (notes.includes(n.path) || folders.some((f) => n.path.startsWith(`${f}/`))) await dropNote(n.path);
    }
    toast(still.length ? `Still kept through its folder (${chosen().folders.find((f) => still[0].startsWith(`${f}/`))})` : 'No longer available offline');
  } };
}

/* ------------------------------------------------------------------ PDFs, one by one */
type Pdf = Pick<Attachment, 'id' | 'url' | 'type'>;
export const pdfsOf = (items: Item[]): Pdf[] => items.flatMap((it) => (it.attachments || []).filter((a) => a.type === 'pdf'));
/** The list of a PDF's annotations (the viewer reads it), saved with the PDF. */
const annotationsUrl = (a: Pdf): string => (a.url.startsWith('/api/note-pdfs/') ? a.url.replace(/\/file$/, '/annotations') : `/api/attachments/${a.id}/annotations`);

/** Saves the PDFs (and their annotations) for offline use, or deletes their saved copies. */
export async function setPdfsOffline(pdfs: Pdf[], on: boolean): Promise<void> {
  const what = pdfs.length === 1 ? 'PDF' : plural(pdfs.length, 'PDF');
  if (!on) {
    await forgetFiles(pdfs.flatMap((a) => [fileUrl(a), annotationsUrl(a)]));
    await refreshSaved();
    toast(`${what === 'PDF' ? 'The PDF' : what} will no longer be available offline`);
    return;
  }
  toast(`Saving ${what === 'PDF' ? 'the PDF' : what} for offline use…`, { timeout: 2500 });
  for (let i = 0; i < pdfs.length; i += CHUNK / 2) {
    await saveFiles(pdfs.slice(i, i + CHUNK / 2).flatMap((a) => [fileUrl(a), annotationsUrl(a)]), true);
  }
  await refreshSaved();
  const missing = pdfs.filter((a) => !pdfSaved(fileUrl(a))).length;
  if (missing) toast(`${plural(missing, 'PDF')} could not be saved for offline use. Try again when Synapse can be reached.`, { type: 'error' });
  else toast(`${what === 'PDF' ? 'The PDF is' : `${what} are`} available offline`, { type: 'success' });
}

/** The menu entry that makes these PDFs available offline, or removes them; none while offline use is off. */
export function offlineMenuEntry(pdfs: Pdf[]): MenuEntry | null {
  pdfs = pdfs.filter((a) => a.type === 'pdf');
  if (!offlineEnabled() || !pdfs.length) return null;
  const all = pdfs.every((a) => pdfSaved(fileUrl(a)));
  const n = pdfs.length > 1 ? ` (${pdfs.length} PDFs)` : '';
  return all
    ? { label: `Remove Offline Copy${n}`, icon: 'download', run: () => { void setPdfsOffline(pdfs, false); } }
    : { label: `Make Available Offline${n}`, icon: 'download', run: () => { void setPdfsOffline(pdfs, true); } };
}

/** Turns offline use on: registers the worker and saves everything. Rejects when this browser cannot do it. */
export async function enableOffline(): Promise<void> {
  if (!offlineSupported()) throw new Error('Offline use needs a secure page: open Synapse over https, or on this computer as localhost.');
  setOfflineFlag(true);
  try {
    await startOffline();
  } catch (e) { setOfflineFlag(false); throw e; }
  // without this, Safari deletes a site's data after a week without a visit, and other browsers when space runs low
  try { await navigator.storage?.persist?.(); } catch { /* not offered: the copy may still be evicted */ }
  window.dispatchEvent(new Event(OFFLINE_EVENT));
  await syncOffline(true).catch((e) => { if (offlineEnabled()) throw e; });   // turned off meanwhile: not an error
}

export async function disableOffline(): Promise<void> {
  await removeOfflineCopy();
  await removeLocalNotes();
  await removeQueue();
  await running?.catch(() => null);   // the save that was running notices and stops
  Object.assign(syncState, { running: false, step: '', done: 0, total: 0, failed: 0 });
  tick();
}

// A rename or deletion while Synapse answers, behind an earlier one that still waits: send them now.
setNotesPusher(() => { setTimeout(() => { void pushNotes(); }, 0); });
setBeforeFlush(() => pushNotes());

// Quietly, a little after the app has started: new papers, and a new version of the app, get saved too.
if (offlineEnabled()) setTimeout(() => { syncOffline().catch(() => null); }, 8000);
window.addEventListener('online', () => { if (offlineEnabled()) setTimeout(() => { syncOffline().catch(() => null); }, 3000); });
// Synapse answers again (the browser may have been online all along): bring the notes' edits in at once.
let wasOffline = false;
window.addEventListener(OFFLINE_EVENT, () => {
  const off = offlineNow();
  if (wasOffline && !off) {
    // the notes first: a task made offline may be shown in a note made offline
    void pushNotes().then(() => (queuedCount() ? flushPlugins() : undefined)).catch(() => null);
  }
  wasOffline = off;
});
