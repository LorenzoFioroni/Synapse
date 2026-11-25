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
// Notes, folders and the PDFs of the notes folder can also be renamed, moved, deleted (and folders
// created) offline: each change is kept, in order, in a log of place changes (``PlaceOp``). The
// listing shows Synapse's notes with the log applied; a kept note's record follows the changes
// (``origin`` is where Synapse has it). offlineSync.ts makes the changes in Synapse in the same
// order, before the notes' edits go in.
import { offlineEnabled, offlineNow } from './offline.js';
import type { NoteFile, NoteMoved, NotesListing } from './types.js';

const DB = 'synapse-offline-notes';
const STORE = 'notes';
const PLACES = 'places';
const CHOSEN_KEY = 'zc.offline.notes';   // {notes: [...paths], folders: [...paths]}
const COALESCE_MS = 2 * 60_000;          // saves this close together are one local version (like the history's IDLE)
export const NOTES_EVENT = 'zc:offline-notes';
/** Synapse made the place changes made offline (or refused some): lists of notes are read again. */
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
  /** Set while place changes wait: where Synapse has the note now (null: Synapse deleted it). */
  origin?: string | null;
  /** Deleted offline (when), and the Undo that brings it back (``restore``). */
  trashed?: number;
  trashId?: string;
}

/** A change of place made offline, made in Synapse later in the same order. */
export type PlaceOp = { seq?: number; at: number } & (
  | { kind: 'mkdir'; path: string }
  | { kind: 'move'; what: 'note' | 'pdf' | 'folder'; from: string; to: string }
  /** ``seen``: what this device knew of the notes and PDFs it deletes (a note's time, a PDF's size):
   *  one changed or added in Synapse meanwhile is not deleted. */
  | { kind: 'trash'; paths: string[]; folders: string[]; trashId: string; seen: Record<string, number> }
);

let mem = new Map<string, LocalNote>();
let ops: PlaceOp[] = [];
let loaded: Promise<void> | null = null;

function idb(): Promise<IDBDatabase> {
  return new Promise((ok, no) => {
    const r = indexedDB.open(DB, 2);
    r.onupgradeneeded = () => {
      const db = r.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'path' });
      if (!db.objectStoreNames.contains(PLACES)) db.createObjectStore(PLACES, { keyPath: 'seq', autoIncrement: true });
    };
    r.onsuccess = () => ok(r.result);
    r.onerror = () => no(r.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T> | void, store = STORE): Promise<T | undefined> {
  const db = await idb();
  return new Promise((ok, no) => {
    const t = db.transaction(store, mode);
    const req = fn(t.objectStore(store));
    t.oncomplete = () => { db.close(); ok(req ? req.result : undefined); };
    t.onerror = () => { db.close(); no(t.error); };
  });
}

const changed = () => window.dispatchEvent(new Event(NOTES_EVENT));

/** Reads the kept notes and the place changes waiting into memory (once). */
export function loadNotes(): Promise<void> {
  if (!offlineEnabled()) return Promise.resolve();
  loaded ||= Promise.all([tx<LocalNote[]>('readonly', (s) => s.getAll()), tx<PlaceOp[]>('readonly', (s) => s.getAll(), PLACES)])
    .then(([all, places]) => {
      mem = new Map((all || []).map((n) => [n.path, n]));
      ops = (places || []).sort((a, b) => a.seq! - b.seq!);
      changed();
    })
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
/** Edits, a change of place or a conflict waiting to go to Synapse. */
export function notePending(path: string): boolean {
  const n = mem.get(path);
  return !!(n && (n.versions.length || n.conflict || n.origin !== undefined || n.trashed));
}
/** Created here, or changed in place here: Synapse must catch up before the note goes there again. */
const structural = (n: LocalNote) => n.baseHash === null || n.origin !== undefined || !!n.trashed;

/** Puts ``n`` (whose path may have changed from ``from``) in one step. */
export async function rekeyNote(from: string, n: LocalNote): Promise<void> {
  mem.delete(from);
  mem.set(n.path, n);
  await tx('readwrite', (s) => { if (from !== n.path) s.delete(from); return s.put(n); });
  changed();
}

/* ------------------------------------------------------------------ the log of place changes */
export const placeOps = (): PlaceOp[] => [...ops];

async function addOp(op: PlaceOp): Promise<void> {
  op.seq = await tx<IDBValidKey>('readwrite', (s) => s.add(op), PLACES) as number;
  ops.push(op);
  changed();
}

/** Synapse made the change (or refused it): it leaves the log. */
export async function dropOp(op: PlaceOp): Promise<void> {
  ops = ops.filter((o) => o !== op);
  await tx('readwrite', (s) => s.delete(op.seq!), PLACES);
  changed();
}

/** Asks offlineSync.ts to send what waits (set there; this module does not import it). */
let pushSoon = (): void => {};
export function setNotesPusher(fn: () => void): void { pushSoon = fn; }

/** Deletes every kept note (offline use turned off). */
export async function removeLocalNotes(): Promise<void> {
  mem = new Map();
  ops = [];
  loaded = null;
  view = null;
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
const within = (path: string, folder: string) => path === folder || inFolder(path, folder);
/** ``p`` after the folder ``from`` became ``to``. */
const remapped = (p: string, from: string, to: string) => (within(p, from) ? to + p.slice(from.length) : p);

/** Whether ``path`` was chosen, itself or through a folder (a note created offline always is). */
export function noteChosen(path: string): boolean {
  const c = chosen();
  return c.notes.includes(path) || c.folders.some((f) => inFolder(path, f)) || mem.get(path)?.baseHash === null;
}

/** A kept note was renamed or moved: it stays kept under its new path. */
function chosenMoved(from: string, to: string): void {
  const c = chosen();
  if (!c.notes.includes(from)) return;
  c.notes = c.notes.filter((p) => p !== from);
  if (!c.folders.some((f) => inFolder(to, f))) c.notes.push(to);
  saveChosen(c);
}

/** A folder was renamed or moved: what was kept in it stays kept. */
function chosenFolderMoved(from: string, to: string): void {
  const c = chosen();
  saveChosen({ notes: c.notes.map((p) => remapped(p, from, to)), folders: c.folders.map((f) => remapped(f, from, to)) });
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

/** Whether a change of place is made here: while earlier ones wait (so they go in in order), for a
 *  note that is here only, or when Synapse cannot be reached (``e``). */
const placeHere = (e?: unknown) => (e === undefined ? ops.length > 0 : offlineEnabled() && unreachable(e));

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
      if (offlineEnabled() && unreachable(e)) throw Object.assign(new Error(NOT_KEPT), { status: 0 });
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

  /** The notes listing: Synapse's, with the place changes made here applied in order, and the
   *  notes created here that Synapse does not have yet. */
  async listing(net: Net<NotesListing>): Promise<NotesListing> {
    await loadNotes();
    let r = await net();
    for (const op of ops) r = applyToListing(r, op);
    const shown = new Set(r.notes.map((x) => x.path));
    const extra: NotesListing['notes'] = localNotes().filter((n) => n.baseHash === null && !n.trashed && !shown.has(n.path)).map((n) => ({
      path: n.path, id: null, title: titleOf(n.text), type: null, paper: null, author: 'human', created: null, updated: null,
      mtime: (n.versions[n.versions.length - 1]?.at ?? Date.now()) / 1000, tags: [],
    }));
    if (extra.length) r = { ...r, notes: [...r.notes, ...extra], folders: withParents(r.folders, extra.map((x) => parentOf(x.path))) };
    view = r;
    return r;
  },

  /** Rename or move a note or a PDF: in Synapse, or here (see ``placeHere``). */
  async move(path: string, to: string, net: Net<NoteMoved>): Promise<NoteMoved> {
    await loadNotes();
    const n = mem.get(path);
    if ((n && structural(n)) || placeHere()) return moveLocally(path, to);
    try {
      const r = await net();
      if (n) { await rekeyNote(path, { ...n, path: r.path }); chosenMoved(path, r.path); }   // edits waiting go in under the new name
      return r;
    } catch (e) {
      if (!placeHere(e)) throw e;
      return moveLocally(path, to);
    }
  },

  /** Rename or move a folder with everything in it. */
  async moveFolder(path: string, to: string, net: Net<NoteMoved>): Promise<NoteMoved> {
    await loadNotes();
    if (placeHere()) return moveFolderLocally(path, to);
    try {
      const r = await net();
      await recordsFolderMoved(path, r.path, false);
      return r;
    } catch (e) {
      if (!placeHere(e)) throw e;
      return moveFolderLocally(path, to);
    }
  },

  /** A new folder. */
  async newFolder(path: string, net: Net<{ path: string }>): Promise<{ path: string }> {
    await loadNotes();
    const local = async () => {
      const clean = cleanFolder(path);
      if (!clean) throw Object.assign(new Error('A folder needs a name.'), { status: 400 });
      if (!view?.folders.includes(clean)) await logOp({ kind: 'mkdir', path: clean, at: Date.now() });
      return { path: clean };
    };
    if (placeHere()) return local();
    try { return await net(); } catch (e) {
      if (!placeHere(e)) throw e;
      return local();
    }
  },

  /** Move notes, PDFs and folders to the notes trash. */
  async trash<T extends { trash_id: string; paths: string[]; pdfs: string[] }>(paths: string[], folders: string[], net: Net<T>): Promise<T | { trash_id: string; paths: string[]; pdfs: string[] }> {
    await loadNotes();
    if (placeHere() || paths.some((p) => { const n = mem.get(p); return !!n && structural(n); })) return trashLocally(paths, folders);
    try {
      const r = await net();
      // deleted in Synapse: nothing of them waits any more
      for (const n of localNotes()) if (paths.includes(n.path) || folders.some((f) => inFolder(n.path, f))) await dropNote(n.path);
      return r;
    } catch (e) {
      if (!placeHere(e)) throw e;
      return trashLocally(paths, folders);
    }
  },

  /** Undo of a deletion: made here (it brings the notes back), or in Synapse. */
  async restore<T>(trashId: string, net: (id: string) => Promise<T>): Promise<T | { paths: string[]; pdfs: string[]; skipped: string[] }> {
    if (!trashId.startsWith(LOCAL_TRASH)) return net(trashId);
    const sent = sentTrash.get(trashId);
    if (sent) return net(sent);   // Synapse made the deletion meanwhile: it undoes it
    await loadNotes();
    const op = ops.find((o) => o.kind === 'trash' && o.trashId === trashId) as Extract<PlaceOp, { kind: 'trash' }> | undefined;
    const back = localNotes().filter((x) => x.trashId === trashId);
    for (const n of back) await putNote({ ...n, trashed: undefined, trashId: undefined });
    if (op) await dropOp(op);
    if (!ops.length) await settleRecords();
    const pdfs = op ? Object.keys(op.seen).filter((p) => /\.pdf$/i.test(p)) : [];
    const notes = [...new Set([...back.map((n) => n.path), ...(op ? Object.keys(op.seen).filter((p) => !/\.pdf$/i.test(p)) : [])])];
    return { paths: notes, pdfs, skipped: [] };
  },

  /** POST a new note: in Synapse, or here while it cannot be reached. */
  async create<T>(data: { title?: string; folder?: string; path?: string; item_id?: string }, net: Net<T>): Promise<T | NoteFile> {
    try { return await net(); } catch (e) {
      if (!offlineEnabled() || !offlineNow() || !unreachable(e) || data.item_id) throw e;
      await loadNotes();
      const title = (data.title || 'Untitled').trim();
      const folder = (data.folder || '').replace(/^\/+|\/+$/g, '');
      let path = data.path || `${folder ? `${folder}/` : ''}${slug(title)}.md`;
      for (let i = 2; taken(path); i++) path = path.replace(/(?: \d+)?\.md$/, ` ${i}.md`);
      const n: LocalNote = { path, baseHash: null, baseText: null, text: `# ${title}\n\n`, versions: [], file: null, conflict: null };
      await saveLocally(n, n.text);
      return asFile(mem.get(path)!);
    }
  },
};

const NOT_KEPT = 'This note is not available offline. Choose Make Available Offline in the notes list while Synapse can be reached.';
const LOCAL_TRASH = 'local-trash:';
/** The listing as last shown (with the changes made here): what names are taken. */
let view: NotesListing | null = null;
/** Deletions made here that Synapse has made since: their Undo goes to Synapse (local id → its id). */
export const sentTrash = new Map<string, string>();

function deletedHere(path: string): Error { return Object.assign(new Error(`“${path}” was deleted on this device.`), { status: 404 }); }
const conflict = (message: string) => Object.assign(new Error(message), { status: 409 });

const parentOf = (p: string) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');
const baseName = (p: string) => p.slice(p.lastIndexOf('/') + 1);
/** ``a/b.md``: no leading slash, ``.md`` once. */
const cleanPath = (p: string) => p.replace(/^\/+/, '').replace(/(?:\.md)?$/i, '.md');
const cleanFolder = (p: string) => p.trim().replace(/^\/+|\/+$/g, '').replace(/\/{2,}/g, '/');
const isPdf = (p: string) => /\.pdf$/i.test(p);

/** ``folders`` with ``more`` and their parent folders, sorted. */
function withParents(folders: string[], more: string[]): string[] {
  const all = new Set(folders);
  for (let f of more) for (; f; f = parentOf(f)) all.add(f);
  return [...all].sort((a, b) => a.localeCompare(b));
}

/** A note, PDF or folder at ``p`` in the listing as shown (or a note kept here). */
function taken(p: string): boolean {
  const low = p.toLowerCase();   // Synapse may run on a disk that ignores case
  const same = (x: string) => x.toLowerCase() === low;
  return localNotes().some((n) => !n.trashed && same(n.path))
    || !!view && (view.notes.some((n) => same(n.path)) || (view.pdfs || []).some((x) => same(x.path)) || view.folders.some(same));
}

/** The listing after ``op``. */
function applyToListing(r: NotesListing, op: PlaceOp): NotesListing {
  if (op.kind === 'mkdir') return { ...r, folders: withParents(r.folders, [op.path]) };
  if (op.kind === 'trash') {
    const gone = (p: string) => op.paths.includes(p) || op.folders.some((f) => within(p, f));
    return { ...r, notes: r.notes.filter((n) => !gone(n.path)), pdfs: r.pdfs?.filter((x) => !gone(x.path)), folders: r.folders.filter((f) => !gone(f)) };
  }
  const { from, to } = op;
  const move = op.what === 'folder' ? (p: string) => remapped(p, from, to) : (p: string) => (p === from ? to : p);
  const colors = r.folder_colors && op.what === 'folder'
    ? Object.fromEntries(Object.entries(r.folder_colors).map(([k, v]) => [move(k), v])) : r.folder_colors;
  return {
    ...r,
    notes: r.notes.map((n) => (move(n.path) === n.path ? n : { ...n, path: move(n.path) })),
    pdfs: r.pdfs?.map((x) => (move(x.path) === x.path ? x : { ...x, path: move(x.path), name: baseName(move(x.path)) })),
    folders: withParents(r.folders.map(move), [parentOf(to)].concat(op.what === 'folder' ? [to] : [])),
    folder_colors: colors,
  };
}

/** Keeps ``op`` to make in Synapse later, and shows it here at once. */
async function logOp(op: PlaceOp): Promise<void> {
  await addOp(op);
  if (view) view = applyToListing(view, op);
  if (!offlineNow()) pushSoon();
}

async function moveLocally(path: string, to: string): Promise<NoteMoved> {
  const pdf = isPdf(path);
  to = pdf ? to.replace(/^\/+/, '') : cleanPath(to);
  const done = { path: to, updated_links: [], broken_links: [] };
  if (to === path) return done;
  if (taken(to) && to.toLowerCase() !== path.toLowerCase()) throw conflict(`There is already ${pdf ? 'a PDF' : 'a note'} “${to}”.`);
  const n = mem.get(path);
  if (n) {
    await rekeyNote(path, { ...n, path: to, origin: n.baseHash === null ? undefined : (n.origin === undefined ? path : n.origin) });
    chosenMoved(path, to);
  }
  // a note created here is created where it is when Synapse answers: nothing to move there
  if (!n || n.baseHash !== null) await logOp({ kind: 'move', what: pdf ? 'pdf' : 'note', from: path, to, at: Date.now() });
  return done;
}

async function moveFolderLocally(path: string, to: string): Promise<NoteMoved> {
  path = cleanFolder(path);
  to = cleanFolder(to);
  const done = { path: to, updated_links: [], broken_links: [] };
  if (to === path) return done;
  if (inFolder(to, path)) throw Object.assign(new Error('A folder cannot be moved into itself'), { status: 400 });
  if (taken(to) && to.toLowerCase() !== path.toLowerCase()) throw conflict(`There is already a folder “${to}”.`);
  await recordsFolderMoved(path, to, true);
  await logOp({ kind: 'move', what: 'folder', from: path, to, at: Date.now() });
  return done;
}

/** The kept notes in the folder ``from`` follow it to ``to`` (``here``: Synapse does not know yet). */
async function recordsFolderMoved(from: string, to: string, here: boolean): Promise<void> {
  for (const n of localNotes().filter((x) => inFolder(x.path, from))) {
    const origin = here && n.baseHash !== null && n.origin === undefined ? n.path : n.origin;
    await rekeyNote(n.path, { ...n, path: remapped(n.path, from, to), origin });
  }
  chosenFolderMoved(from, to);
}

async function trashLocally(paths: string[], folders: string[]): Promise<{ trash_id: string; paths: string[]; pdfs: string[] }> {
  const trashId = `${LOCAL_TRASH}${Date.now().toString(36)}${(localSeq++).toString(36)}`;
  const gone = (p: string) => paths.includes(p) || folders.some((f) => inFolder(p, f));
  const seen: Record<string, number> = {};
  for (const n of view?.notes || []) if (gone(n.path) && mem.get(n.path)?.baseHash !== null) seen[n.path] = n.mtime;
  for (const x of view?.pdfs || []) if (gone(x.path)) seen[x.path] = x.size;
  const now = Date.now();
  const records = localNotes().filter((n) => !n.trashed && gone(n.path));
  for (const n of records) await putNote({ ...n, trashed: now, trashId, origin: n.baseHash === null ? undefined : (n.origin === undefined ? n.path : n.origin) });
  const sent = { paths: paths.filter((p) => mem.get(p)?.baseHash !== null), folders };
  if (sent.paths.length || sent.folders.length) await logOp({ kind: 'trash', ...sent, trashId, seen, at: now });
  const all = [...new Set([...Object.keys(seen), ...records.map((n) => n.path)])];
  return { trash_id: trashId, paths: all.filter((p) => !isPdf(p)), pdfs: all.filter(isPdf) };
}

/** Once no place change waits, each kept note is where Synapse has it: a change Synapse refused is
 *  undone here, a note it deleted is forgotten. Returns the notes that went back to their place. */
export async function settleRecords(): Promise<string[]> {
  const back: string[] = [];
  for (const n of localNotes()) {
    if (n.baseHash === null) {
      if (n.trashed) await dropNote(n.path);   // created and deleted here
      continue;
    }
    if (n.origin === undefined) continue;
    if (n.origin === null) {
      if (n.trashed || !n.versions.length) { await dropNote(n.path); setChosen([n.path], [], false); continue; }
      await putNote({ ...n, origin: undefined });   // deleted in Synapse meanwhile: its edits ask (apply_offline says "deleted")
      continue;
    }
    const settled = { ...n, origin: undefined, trashed: undefined, trashId: undefined };
    if (n.origin === n.path && !n.trashed) { await putNote(settled); continue; }
    back.push(n.path);
    await rekeyNote(n.path, { ...settled, path: n.origin });
    if (!noteChosen(n.origin)) { setChosen([n.path], [], false); setChosen([n.origin], [], true); }
  }
  return back;
}

/** Synapse made ``op``: the kept notes' ``origin`` follows (``gone``: what it deleted). */
export async function originsAfter(op: PlaceOp, gone: string[] = []): Promise<void> {
  for (const n of localNotes()) {
    if (n.origin == null) continue;
    let origin: string | null = n.origin;
    if (op.kind === 'move') origin = op.what === 'folder' ? remapped(origin, op.from, op.to) : origin === op.from ? op.to : origin;
    else if (op.kind === 'trash') origin = gone.includes(origin) ? null : origin;
    if (origin !== n.origin) await putNote({ ...n, origin });
  }
}

/** Synapse no longer has what ``op`` was about (404): the kept notes in it were deleted there. */
export async function originsGone(op: PlaceOp): Promise<void> {
  if (op.kind !== 'move') return;
  for (const n of localNotes()) {
    if (n.origin != null && (op.what === 'folder' ? inFolder(n.origin, op.from) : n.origin === op.from)) await putNote({ ...n, origin: null });
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
