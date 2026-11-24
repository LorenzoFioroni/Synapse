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
//
// A kept note can also be renamed, moved to another folder or deleted offline: the change is kept
// on its record (``movedFrom``, ``trashed``) and the listing shows it; offlineSync.ts makes it in
// Synapse first, before the note's edits go in. Folders and PDFs still need Synapse.
import { offlineEnabled, offlineNow } from './offline.js';
import type { NoteFile, NoteMoved, NotesListing } from './types.js';

const DB = 'synapse-offline-notes';
const STORE = 'notes';
const CHOSEN_KEY = 'zc.offline.notes';   // {notes: [...paths], folders: [...paths]}
const COALESCE_MS = 2 * 60_000;          // saves this close together are one local version (like the history's IDLE)
export const NOTES_EVENT = 'zc:offline-notes';
/** Synapse made the renames and deletions done offline (or refused some): lists of notes are read again. */
export const PLACES_EVENT = 'zc:offline-notes-places';

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
  /** Renamed or moved offline: where Synapse has the note (its path is where it is going). */
  movedFrom?: string;
  movedAt?: number;
  /** Deleted offline (when), and the Undo that brings it back (``restore``). */
  trashed?: number;
  trashId?: string;
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
/** Edits, a rename or move, a deletion or a conflict waiting to go to Synapse. */
export function notePending(path: string): boolean {
  const n = mem.get(path);
  return !!(n && (n.versions.length || n.conflict || n.movedFrom || n.trashed));
}
/** A change Synapse must make before anything else about the note: it was created, renamed or deleted here. */
const structural = (n: LocalNote) => n.baseHash === null || !!n.movedFrom || !!n.trashed;
/** Where Synapse has the note now. */
export const serverPath = (n: LocalNote): string => n.movedFrom ?? n.path;

/** Puts ``n`` (whose path may have changed from ``from``) in one step. */
export async function rekeyNote(from: string, n: LocalNote): Promise<void> {
  mem.delete(from);
  mem.set(n.path, n);
  await tx('readwrite', (s) => { if (from !== n.path) s.delete(from); return s.put(n); });
  changed();
}

/** Asks offlineSync.ts to send what waits (set there; this module does not import it). */
let pushSoon = (): void => {};
export function setNotesPusher(fn: () => void): void { pushSoon = fn; }

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

/** A kept note was renamed or moved: it stays kept under its new path. */
function chosenMoved(from: string, to: string): void {
  const c = chosen();
  c.notes = c.notes.filter((p) => p !== from);
  if (!c.folders.some((f) => inFolder(to, f))) c.notes.push(to);
  saveChosen(c);
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
    if (n?.trashed) throw deletedHere(path);
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
    if (n?.trashed) throw deletedHere(path);
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
    if (n?.trashed) throw deletedHere(path);
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
    known = new Set([...r.notes.map((x) => x.path), ...(r.pdfs || []).map((x) => x.path)]);
    const byPath = new Map(r.notes.map((x) => [x.path, x]));
    // what changed here and is not in Synapse yet: notes created, renamed or moved, deleted
    const gone = new Set(localNotes().filter((n) => n.baseHash !== null && (n.trashed || n.movedFrom)).map(serverPath));
    const extra: NotesListing['notes'] = [];
    for (const n of localNotes()) {
      if (n.trashed || (n.baseHash !== null && !n.movedFrom) || (byPath.has(n.path) && !gone.has(n.path))) continue;
      const was = byPath.get(serverPath(n));
      const last = n.versions[n.versions.length - 1];
      extra.push(was ? { ...was, path: n.path, title: titleOf(n.text) ?? was.title } : {
        path: n.path, id: null, title: titleOf(n.text), type: null, paper: null, author: 'human', created: null, updated: null, mtime: (last?.at ?? Date.now()) / 1000, tags: [],
      });
    }
    if (!extra.length && !gone.size) return r;
    const shown = new Set(extra.map((x) => x.path));
    return { ...r, notes: [...r.notes.filter((x) => !gone.has(x.path) && !shown.has(x.path)), ...extra] };
  },

  /** Rename or move a note: in Synapse, or here when it cannot be reached (only a note kept here),
   *  and here while an earlier change of the note waits, so they go in in order. */
  async move(path: string, to: string, net: Net<NoteMoved>): Promise<NoteMoved> {
    await loadNotes();
    const n = mem.get(path);
    if (n && structural(n)) return moveLocally(n, to);
    try {
      const r = await net();
      if (n) { await rekeyNote(path, { ...n, path: r.path }); chosenMoved(path, r.path); }   // edits waiting go in under the new name
      return r;
    } catch (e) {
      if (!offlineEnabled() || !unreachable(e)) throw e;
      if (!n) throw needsSynapse(/\.pdf$/i.test(path) ? 'A PDF can be renamed or moved only while Synapse can be reached.' : NOT_KEPT);
      return moveLocally(n, to);
    }
  },

  /** Move notes to the notes trash: in Synapse, or here (notes kept here only, no folders or PDFs). */
  async trash<T extends { trash_id: string; paths: string[]; pdfs: string[] }>(paths: string[], folders: string[], net: Net<T>): Promise<T | { trash_id: string; paths: string[]; pdfs: string[] }> {
    await loadNotes();
    const local = paths.map((p) => mem.get(p));
    const allKept = !folders.length && local.every((n) => n && !n.trashed);
    if (allKept && local.some((n) => n && structural(n))) return trashLocally(local as LocalNote[]);
    try {
      const r = await net();
      for (const n of local) if (n) await dropNote(n.path);   // deleted in Synapse: nothing of it waits any more
      return r;
    } catch (e) {
      if (!offlineEnabled() || !unreachable(e)) throw e;
      if (folders.length) throw needsSynapse('Folders can be deleted only while Synapse can be reached.');
      if (!allKept) throw needsSynapse(paths.some((p) => /\.pdf$/i.test(p)) ? 'PDFs can be deleted only while Synapse can be reached.' : NOT_KEPT);
      return trashLocally(local as LocalNote[]);
    }
  },

  /** Undo of a deletion: made here (it brings the notes back), or in Synapse. */
  async restore<T>(trashId: string, net: Net<T>): Promise<T | { paths: string[]; pdfs: string[]; skipped: string[] }> {
    if (!trashId.startsWith(LOCAL_TRASH)) return net();
    await loadNotes();
    const back = localNotes().filter((x) => x.trashId === trashId);
    for (const n of back) await putNote({ ...n, trashed: undefined, trashId: undefined });
    return { paths: back.map((n) => n.path), pdfs: [], skipped: [] };
  },

  /** POST a new note: in Synapse, or here while it cannot be reached. */
  async create<T>(data: { title?: string; folder?: string; path?: string; item_id?: string }, net: Net<T>): Promise<T | NoteFile> {
    try { return await net(); } catch (e) {
      if (!offlineEnabled() || !offlineNow() || !unreachable(e) || data.item_id) throw e;
      await loadNotes();
      const title = (data.title || 'Untitled').trim();
      const folder = (data.folder || '').replace(/^\/+|\/+$/g, '');
      let path = data.path || `${folder ? `${folder}/` : ''}${slug(title)}.md`;
      for (let i = 2; mem.has(path) || known.has(path); i++) path = path.replace(/(?: \d+)?\.md$/, ` ${i}.md`);
      const n: LocalNote = { path, baseHash: null, baseText: null, text: `# ${title}\n\n`, versions: [], file: null, conflict: null };
      await saveLocally(n, n.text);
      return asFile(mem.get(path)!);
    }
  },
};

const NOT_KEPT = 'This note is not available offline. Choose Make Available Offline in the notes list while Synapse can be reached.';
const LOCAL_TRASH = 'local-trash:';
/** The notes and PDFs Synapse had at the last listing (a name already taken). */
let known = new Set<string>();

function needsSynapse(message: string): Error { return Object.assign(new Error(message), { status: 0 }); }
function deletedHere(path: string): Error { return Object.assign(new Error(`“${path}” was deleted on this device.`), { status: 404 }); }

/** ``a/b.md``: no leading slash, ``.md`` once. */
const cleanPath = (p: string) => p.replace(/^\/+/, '').replace(/(?:\.md)?$/i, '.md');

async function moveLocally(n: LocalNote, to: string): Promise<NoteMoved> {
  to = cleanPath(to);
  const done = { path: to, updated_links: [], broken_links: [] };
  if (to === n.path) return done;
  // a note deleted here keeps its place until Synapse deletes it (Undo brings it back there)
  const taken = mem.has(to)
    || (known.has(to) && !localNotes().some((x) => x.movedFrom === to || (x.trashed && serverPath(x) === to)));
  if (taken) throw Object.assign(new Error(`There is already a note “${to}”.`), { status: 409 });
  const from = n.baseHash === null ? undefined : serverPath(n);
  const moved: LocalNote = { ...n, path: to, movedFrom: from === to ? undefined : from, movedAt: Date.now() };
  await rekeyNote(n.path, moved);
  chosenMoved(n.path, to);
  if (!offlineNow()) pushSoon();
  return done;
}

async function trashLocally(notes: LocalNote[]): Promise<{ trash_id: string; paths: string[]; pdfs: string[] }> {
  const trashId = `${LOCAL_TRASH}${Date.now().toString(36)}`;
  for (const n of notes) await putNote({ ...n, trashed: Date.now(), trashId });
  if (!offlineNow()) pushSoon();
  return { trash_id: trashId, paths: notes.map((n) => n.path), pdfs: [] };
}

/** A change of folders, which needs Synapse: said plainly when it cannot be reached. */
export async function folderChange<T>(net: Net<T>): Promise<T> {
  try { return await net(); } catch (e) {
    if (offlineEnabled() && unreachable(e)) throw needsSynapse('Folders can be changed only while Synapse can be reached.');
    throw e;
  }
}

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
