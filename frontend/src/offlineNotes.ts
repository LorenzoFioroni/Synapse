// Notes kept for offline use (docs/offline.md). The user chooses notes or folders ("Make Available
// Offline"); their text is kept in IndexedDB, never in the service worker's shared cache, and a
// locked note never. While Synapse cannot be reached, a kept note opens from here and its saves
// become local versions, each with the time it was made: the note's local history. Once Synapse
// answers again, offlineSync.ts sends them (POST /api/notes/offline), which records each as a
// commit by this device or merges it with what changed meanwhile, and asks the user on a conflict.
//
// The note calls of api.ts go through ``notesOffline`` here, so the editor (NoteSession) and the
// phone need no changes: a local save answers with a local hash ("local:…"), and once the edits
// are in, the session's regular check sees the note's real hash and takes its text.
import { offlineEnabled, offlineNow } from './offline.js';
import type { NoteFile, NotesListing } from './types.js';

const DB = 'synapse-offline-notes';
const STORE = 'notes';
const CHOSEN_KEY = 'zc.offline.notes';   // {notes: [...paths], folders: [...paths]}
const COALESCE_MS = 2 * 60_000;          // saves this close together are one local version (like the history's IDLE)
export const NOTES_EVENT = 'zc:offline-notes';

export interface LocalVersion { text: string; at: number }

/** What the server said when the edits could not be brought in as they were. */
export interface NoteConflict {
  status: 'conflict' | 'deleted';
  hash?: string;        // the note as it is now in Synapse
  current?: string;
  base?: string;
  mine: string;         // the last offline version
  merged?: string;      // the three-way merge, with Git's conflict markers
}

export interface LocalNote {
  path: string;
  /** The note as Synapse last had it here (null: created offline). */
  baseHash: string | null;
  baseText: string | null;
  /** What the editor shows: the last local version, or the note as Synapse had it. */
  text: string;
  /** Offline versions, oldest first, not yet in Synapse. */
  versions: LocalVersion[];
  /** The last answer about the note (its index entry, lock state), to open it without Synapse. */
  file: Partial<NoteFile> | null;
  conflict?: NoteConflict | null;
  /** The hash the last local save answered with, and the server's hash it became (see ``sentHash``). */
  localHash?: string;
  synced?: { local: string; hash: string };
}

let mem = new Map<string, LocalNote>();
let loaded: Promise<void> | null = null;

function idb(): Promise<IDBDatabase> {
  return new Promise((ok, no) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: 'path' });
    r.onsuccess = () => ok(r.result);
    r.onerror = () => no(r.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T> | void): Promise<T | undefined> {
  const db = await idb();
  return new Promise((ok, no) => {
    const t = db.transaction(STORE, mode);
    const req = fn(t.objectStore(STORE));
    t.oncomplete = () => { db.close(); ok(req ? req.result : undefined); };
    t.onerror = () => { db.close(); no(t.error); };
  });
}

const changed = () => window.dispatchEvent(new Event(NOTES_EVENT));

/** Reads the kept notes into memory (once). */
export function loadNotes(): Promise<void> {
  if (!offlineEnabled()) return Promise.resolve();
  loaded ||= tx<LocalNote[]>('readonly', (s) => s.getAll())
    .then((all) => { mem = new Map((all || []).map((n) => [n.path, n])); changed(); })
    .catch(() => { loaded = null; });
  return loaded;
}

export async function putNote(n: LocalNote): Promise<void> {
  mem.set(n.path, n);
  await tx('readwrite', (s) => s.put(n));
  changed();
}

export async function dropNote(path: string): Promise<void> {
  mem.delete(path);
  await tx('readwrite', (s) => s.delete(path));
  changed();
}

export const localNote = (path: string): LocalNote | undefined => mem.get(path);
export const localNotes = (): LocalNote[] => [...mem.values()];
/** Edits or a conflict waiting to go to Synapse. */
export const notePending = (path: string): boolean => !!(mem.get(path)?.versions.length || mem.get(path)?.conflict);

/** Deletes every kept note (offline use turned off). */
export async function removeLocalNotes(): Promise<void> {
  mem = new Map();
  loaded = null;
  try { localStorage.removeItem(CHOSEN_KEY); } catch { /* private mode */ }
  await new Promise<void>((done) => {
    try {
      const del = indexedDB.deleteDatabase(DB);
      del.onsuccess = del.onerror = del.onblocked = () => done();
    } catch { done(); }
  });
  changed();
}

/* ------------------------------------------------------------------ which notes */
interface Chosen { notes: string[]; folders: string[] }

export function chosen(): Chosen {
  try {
    const c = JSON.parse(localStorage.getItem(CHOSEN_KEY) || 'null') as Chosen | null;
    return { notes: c?.notes || [], folders: c?.folders || [] };
  } catch { return { notes: [], folders: [] }; }
}

function saveChosen(c: Chosen): void {
  try { localStorage.setItem(CHOSEN_KEY, JSON.stringify(c)); } catch { /* private mode */ }
  changed();
}

const inFolder = (path: string, folder: string) => path.startsWith(folder.replace(/\/?$/, '/'));

/** Whether ``path`` was chosen, itself or through a folder (a note created offline always is). */
export function noteChosen(path: string): boolean {
  const c = chosen();
  return c.notes.includes(path) || c.folders.some((f) => inFolder(path, f)) || mem.get(path)?.baseHash === null;
}

export function folderChosen(folder: string): boolean {
  return chosen().folders.some((f) => f === folder || inFolder(folder, f));
}

/** Choose notes and folders for offline use, or stop keeping them (their local copies go once nothing waits). */
export function setChosen(notes: string[], folders: string[], on: boolean): void {
  const c = chosen();
  if (on) {
    c.notes = [...new Set([...c.notes, ...notes])];
    c.folders = [...new Set([...c.folders, ...folders])];
  } else {
    c.notes = c.notes.filter((p) => !notes.includes(p));
    c.folders = c.folders.filter((f) => !folders.some((x) => f === x || inFolder(f, x)));
    // a note kept through a folder that stays chosen cannot be left out on its own: say so by the menu
  }
  saveChosen(c);
}

/** The notes of the listing that are chosen. */
export const chosenPaths = (listing: NotesListing): string[] => listing.notes.map((n) => n.path).filter(noteChosen);

/* ------------------------------------------------------------------ the note calls */
type Net<T> = () => Promise<T>;

/** True for an error that means Synapse could not be reached (not an answer it gave). */
export function unreachable(e: unknown): boolean {
  const s = (e as { status?: number } | null)?.status;
  return s === 0 || s === 503 || s === undefined;
}

let localSeq = 0;
const newLocalHash = () => `local:${Date.now().toString(36)}${(localSeq++).toString(36)}`;

function asFile(n: LocalNote): NoteFile {
  return { ...(n.file || {}), path: n.path, text: n.text, hash: n.localHash || n.baseHash || newLocalHash(), info: n.file?.info ?? null } as NoteFile;
}

/** Keeps the latest copy of a chosen note that is not locked; forgets a locked one. */
async function remember(r: NoteFile): Promise<void> {
  if (!offlineEnabled() || !noteChosen(r.path) || notePending(r.path)) return;
  if (r.locked) { if (mem.has(r.path)) await dropNote(r.path); return; }
  const old = mem.get(r.path);
  if (old && old.baseHash === r.hash && old.text === r.text) return;
  await putNote({ path: r.path, baseHash: r.hash, baseText: r.text, text: r.text, versions: [], file: { info: r.info }, conflict: null });
}

export const notesOffline = {
  /** GET a note: from Synapse when it answers (kept up to date here); from here when it does not,
   *  or while offline edits wait to go in. */
  async get(path: string, net: Net<NoteFile>): Promise<NoteFile> {
    await loadNotes();
    const n = mem.get(path);
    if (n && notePending(path)) return asFile(n);
    try {
      const r = await net();
      void remember(r);
      return r;
    } catch (e) {
      if (n && offlineEnabled() && unreachable(e)) return asFile(n);
      if (offlineEnabled() && unreachable(e)) throw Object.assign(new Error('This note is not available offline. Choose Make Available Offline in the notes list while Synapse can be reached.'), { status: 0 });
      throw e;
    }
  },

  /** The cheap check: unchanged while the note is local. */
  async check<T>(path: string, hash: string, net: Net<T>): Promise<T | { path: string; hash: string; unchanged: true }> {
    await loadNotes();
    const n = mem.get(path);
    if (n && notePending(path)) return { path, hash: n.localHash || hash, unchanged: true };
    try { return await net(); } catch (e) {
      if (n && offlineEnabled() && unreachable(e)) return { path, hash, unchanged: true };
      throw e;
    }
  },

  /** PUT a note: to Synapse; to a local version when it cannot be reached (or edits already wait). */
  async save<T extends { hash: string }>(path: string, text: string, hash: string | null, net: (hash: string | null) => Promise<T>): Promise<T | { path: string; hash: string; info: null }> {
    await loadNotes();
    const n = mem.get(path);
    if (n && !notePending(path) && hash && n.synced?.local === hash) hash = n.synced.hash;   // the local version that went in
    if (n && notePending(path)) return saveLocally(n, text);
    try {
      const r = await net(hash);
      if (n) await putNote({ ...n, baseHash: r.hash, baseText: text, text, versions: [], conflict: null });
      return r;
    } catch (e) {
      if (n && offlineEnabled() && unreachable(e)) return saveLocally(n, text);
      throw e;
    }
  },

  /** The notes listing, with the notes created offline that Synapse does not know yet. */
  async listing(net: Net<NotesListing>): Promise<NotesListing> {
    await loadNotes();
    const r = await net();
    const known = new Set(r.notes.map((x) => x.path));
    const extra = localNotes().filter((n) => n.baseHash === null && !known.has(n.path));
    if (!extra.length) return r;
    const now = Date.now() / 1000;
    return { ...r, notes: [...r.notes, ...extra.map((n) => ({ path: n.path, id: null, title: titleOf(n.text), type: null, paper: null, author: 'human', created: null, updated: null, mtime: now, tags: [] }))] };
  },

  /** POST a new note: in Synapse, or here while it cannot be reached. */
  async create<T>(data: { title?: string; folder?: string; path?: string; item_id?: string }, net: Net<T>): Promise<T | NoteFile> {
    try { return await net(); } catch (e) {
      if (!offlineEnabled() || !offlineNow() || !unreachable(e) || data.item_id) throw e;
      await loadNotes();
      const title = (data.title || 'Untitled').trim();
      const folder = (data.folder || '').replace(/^\/+|\/+$/g, '');
      let path = data.path || `${folder ? `${folder}/` : ''}${slug(title)}.md`;
      for (let i = 2; mem.has(path); i++) path = path.replace(/(?: \d+)?\.md$/, ` ${i}.md`);
      const n: LocalNote = { path, baseHash: null, baseText: null, text: `# ${title}\n\n`, versions: [], file: null, conflict: null };
      await saveLocally(n, n.text);
      return asFile(mem.get(path)!);
    }
  },
};

async function saveLocally(n: LocalNote, text: string): Promise<{ path: string; hash: string; info: null }> {
  const now = Date.now();
  const versions = [...n.versions];
  const last = versions[versions.length - 1];
  if (last && now - last.at < COALESCE_MS) versions[versions.length - 1] = { text, at: now };
  else versions.push({ text, at: now });
  const localHash = newLocalHash();
  await putNote({ ...n, text, versions, localHash });
  return { path: n.path, hash: localHash, info: null };
}

const titleOf = (text: string) => /^#\s+(.+)$/m.exec(text)?.[1]?.trim() || null;
const slug = (title: string) => title.replace(/[\\/:*?"<>|#^[\]]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 80) || 'Untitled';
