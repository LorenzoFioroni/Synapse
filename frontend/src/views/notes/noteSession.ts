// Save logic for one open note, kept free of DOM code so it can be unit-tested
// (tests/js/noteSession.test.mjs).
//
// Guarantees:
//  * Every save sends the hash of the version the editor started from; the server refuses
//    it (409) when the file changed in the meantime, and we enter the "conflict" state
//    instead of overwriting.
//  * While there are unsaved changes, a draft {text, baseHash} is kept in local storage,
//    so a crash, a closed tab or a stopped server never loses typing. The draft is removed
//    as soon as the server has the same text.
//  * Network or server errors keep the text and retry with backoff ("offline").
//
export type SaveStatus = 'saved' | 'dirty' | 'saving' | 'offline' | 'conflict';

/** The server calls a session needs (injectable for tests). Errors carry the HTTP ``status``. */
export interface NoteSessionApi {
  save(path: string, text: string, hash: string | null): Promise<{ hash: string }>;
  create(path: string, text: string): Promise<{ hash: string }>;
  get(path: string): Promise<{ text: string; hash: string }>;
}

/** Where drafts are kept (local storage in the app). */
export interface DraftStore {
  get(key: string): Draft | null | undefined;
  set(key: string, value: Draft): void;
  remove(key: string): void;
}

export interface Draft { text: string; baseHash: string | null; at?: number }

export interface Conflict { mine: string; theirs: string | null; theirsHash: string | null; deleted: boolean }

export interface NoteSessionOptions {
  path: string;
  text: string;                      // what the editor starts with
  hash: string | null;               // server hash of serverText
  serverText?: string;               // default: text
  api: NoteSessionApi;
  drafts: DraftStore;
  delay?: number;                    // ms without typing before saving (default 1500)
  onStatus?: (status: SaveStatus, session: NoteSession) => void;
  onConflict?: (info: Conflict) => void;
  onRemoteText?: (text: string) => void;   // replace the editor content
  onSaved?: (hash: string | null) => void;
  /** The note was moved elsewhere (another window, device or a citation-key rename) to ``to``:
   *  reopen it there, carrying ``text``. Resolves false when the caller cannot follow it. */
  onMoved?: (to: string, text: string) => boolean | Promise<boolean>;
}

const draftKey = (path: string) => `zc.noteDraft:${path}`;

export class NoteSession {
  path: string;
  api: NoteSessionApi;
  drafts: DraftStore;
  delay: number;
  onStatus: (status: SaveStatus, session: NoteSession) => void;
  onConflict: (info: Conflict) => void;
  onRemoteText: (text: string) => void;
  onSaved: (hash: string | null) => void;
  onMoved: ((to: string, text: string) => boolean | Promise<boolean>) | null;
  saved: { text: string; hash: string | null };
  text: string;
  status: SaveStatus;
  timer: ReturnType<typeof setTimeout> | null;
  inFlight: Promise<void> | null;
  again: boolean;
  retryMs: number;
  disposed: boolean;
  conflict: Conflict | null;
  private remoteCheck: Promise<void> | null = null;
  private moved = false;

  constructor(o: NoteSessionOptions) {
    this.path = o.path;
    this.api = o.api;
    this.drafts = o.drafts;
    this.delay = o.delay ?? 1500;
    this.onStatus = o.onStatus || (() => {});
    this.onConflict = o.onConflict || (() => {});
    this.onRemoteText = o.onRemoteText || (() => {});
    this.onMoved = o.onMoved || null;
    this.onSaved = o.onSaved || (() => {});
    this.saved = { text: o.serverText ?? o.text, hash: o.hash };
    this.text = o.text;
    this.status = 'saved';
    this.timer = null;
    this.inFlight = null;
    this.again = false;
    this.retryMs = 0;
    this.disposed = false;
    this.conflict = null;
    if (this.text !== this.saved.text) this._dirty();
  }

  /** Decide what to show when a note is opened and a draft may exist. */
  static open(path: string, server: { text: string; hash: string | null }, drafts: DraftStore): { text: string; restored: boolean; conflict: boolean } {
    const d = drafts.get(draftKey(path));
    if (!d || typeof d.text !== 'string') return { text: server.text, restored: false, conflict: false };
    if (d.text === server.text) { drafts.remove(draftKey(path)); return { text: server.text, restored: false, conflict: false }; }
    if (d.baseHash === server.hash) return { text: d.text, restored: true, conflict: false };
    return { text: d.text, restored: true, conflict: true };   // the file changed since the draft was made
  }

  _set(status: SaveStatus) {
    if (this.status === status) return;
    this.status = status;
    this.onStatus(status, this);
  }

  _dirty() {
    this.drafts.set(draftKey(this.path), { text: this.text, baseHash: this.saved.hash, at: Date.now() });
    if (this.conflict) return;
    this._set(this.retryMs ? 'offline' : 'dirty');
    this._schedule(this.retryMs || this.delay);
  }

  _clean() {
    this.drafts.remove(draftKey(this.path));
    clearTimeout(this.timer ?? undefined);
    this.timer = null;
    if (!this.conflict) this._set(this.inFlight && this.status === 'saving' ? 'saving' : 'saved');
  }

  _schedule(ms: number) {
    clearTimeout(this.timer ?? undefined);
    if (this.conflict || this.disposed) return;
    this.timer = setTimeout(() => { this.timer = null; this.save(); }, ms);
  }

  get dirty() { return this.text !== this.saved.text; }

  /** Call on every editor change. */
  update(text: string): void {
    this.text = text;
    if (this.conflict) {   // keep the draft current, but never save
      this.drafts.set(draftKey(this.path), { text, baseHash: this.saved.hash, at: Date.now() });
      return;
    }
    if (text === this.saved.text) this._clean(); else this._dirty();
  }

  /** Save now if needed. Resolves when the server has the current text (or saving failed). */
  async save(): Promise<void> {
    if (this.moved) return;   // its text went on with the note's new session
    if (this.disposed && !this.dirty) return;
    if (this.conflict || !this.dirty) return;
    if (this.inFlight) { this.again = true; return this.inFlight; }
    clearTimeout(this.timer ?? undefined);
    this.timer = null;
    const sent = this.text;
    this._set('saving');
    this.inFlight = (async () => {
      try {
        const res = await this.api.save(this.path, sent, this.saved.hash);
        this.saved = { text: sent, hash: res.hash };
        this.retryMs = 0;
        this.inFlight = null;
        this.onSaved(res.hash);
        if (this.text === sent) this._clean(); else this._dirty();
      } catch (e) {
        if ((e as { status?: number } | null)?.status === 409) {
          await this._enterConflict();
        } else {
          this.retryMs = Math.min(30000, this.retryMs ? this.retryMs * 2 : 2000);
          this.inFlight = null;
          this._dirty();
        }
      } finally {
        this.inFlight = null;
      }
      if (this.again) { this.again = false; if (this.dirty && !this.conflict) await this.save(); }
    })();
    return this.inFlight;
  }

  async _enterConflict() {
    let theirs: string | null = null, theirsHash: string | null = null, deleted = false;
    try {
      const r = await this.api.get(this.path);
      theirs = r.text; theirsHash = r.hash;
    } catch (e) {
      if ((e as { status?: number } | null)?.status === 404) {
        if (await this._followMove(e)) return;
        deleted = true;
      }
    }
    if (!deleted && theirs === this.text) {   // the other side wrote exactly our text
      this.saved = { text: theirs, hash: theirsHash };
      this.inFlight = null;
      this._clean();
      return;
    }
    clearTimeout(this.timer ?? undefined);
    this.conflict = { mine: this.text, theirs, theirsHash, deleted };
    this.drafts.set(draftKey(this.path), { text: this.text, baseHash: this.saved.hash, at: Date.now() });
    this._set('conflict');
    this.onConflict(this.conflict);
  }

  /** Enter the conflict state directly (used when a draft is older than the file). */
  async startConflict() { await this._enterConflict(); }

  /** 'mine' overwrites the file with the editor text; 'theirs' loads the file into the editor.
   *  If this fails (the file still cannot be read, or writing it again fails), the session stays
   *  in conflict and the error is thrown, so the caller can say so and ask again. */
  async resolve(choice: 'mine' | 'theirs'): Promise<void> {
    let c = this.conflict;
    if (!c) return;
    if (!c.deleted && c.theirs === null) {   // the file could not be read when the conflict began
      try {
        const r = await this.api.get(this.path);
        c = { ...c, theirs: r.text, theirsHash: r.hash };
      } catch (e) {
        if ((e as { status?: number } | null)?.status !== 404) throw e;
        c = { ...c, deleted: true };
      }
      this.conflict = c;
    }
    if (choice === 'theirs' && c.deleted) {   // there is no saved file to use any more: ask again
      this.onConflict(c);
      return;
    }
    if (choice === 'theirs') {
      this.conflict = null;
      this.saved = { text: c.theirs as string, hash: c.theirsHash };
      this.text = c.theirs as string;
      this.onRemoteText(this.text);
      this._clean();
      return;
    }
    if (c.deleted) {   // the file was deleted elsewhere: write it again (still in conflict meanwhile)
      const sent = this.text;
      let res: { hash: string };
      try {
        res = await this.api.create(this.path, sent);
      } catch (e) {
        // someone else wrote it again in the meantime: that is a conflict with their text
        if ((e as { status?: number } | null)?.status === 409) { this.conflict = null; await this._enterConflict(); return; }
        throw e;
      }
      this.conflict = null;
      this.saved = { text: sent, hash: res.hash };
      this.onSaved(res.hash);
      if (this.text === sent) this._clean(); else this._dirty();
      return;
    }
    // keep mine: base the next save on the file's current version
    this.conflict = null;
    this.saved = { text: c.theirs as string, hash: c.theirsHash };
    if (this.text === this.saved.text) { this._clean(); return; }
    this._dirty();
    await this.save();
  }

  /** Pick up outside edits; unsaved typing goes through the existing conflict dialog. */
  async checkRemote(): Promise<void> {
    if (this.remoteCheck) return this.remoteCheck;
    this.remoteCheck = this._checkRemote();
    try { await this.remoteCheck; } finally { this.remoteCheck = null; }
  }

  private async _checkRemote(): Promise<void> {
    if (this.disposed || this.moved || this.conflict || this.inFlight) return;
    let r: { text: string; hash: string };
    try { r = await this.api.get(this.path); } catch (e) {
      if ((e as { status?: number } | null)?.status === 404 && !this.disposed && !this.conflict && !this.inFlight) {
        if (await this._followMove(e)) return;
        await this._enterConflict();
      }
      return;
    }
    if (this.disposed || this.conflict || this.inFlight || r.hash === this.saved.hash) return;
    if (this.dirty) { await this._enterConflict(); return; }
    this.saved = { text: r.text, hash: r.hash };
    this.text = r.text;
    this.onRemoteText(r.text);
    this.onSaved(r.hash);
  }

  /** A 404 that says where the note went: follow it when that loses nothing, i.e. nothing is
   *  unsaved here or the moved note is still the version this session last saved. Otherwise
   *  (both sides changed) the caller shows the usual conflict. */
  private async _followMove(e: unknown): Promise<boolean> {
    const to = (e as { data?: { moved_to?: unknown } } | null)?.data?.moved_to;
    if (typeof to !== 'string' || !to || !this.onMoved || this.disposed || this.moved) return false;
    if (this.dirty) {
      let r: { text: string; hash: string };
      try { r = await this.api.get(to); } catch { return false; }
      if (r.hash !== this.saved.hash) return false;
    }
    const from = this.path;
    clearTimeout(this.timer ?? undefined);
    this.moved = true;
    let followed = false;
    try { followed = await this.onMoved(to, this.text); } catch { /* treat as not followed */ }
    if (!followed) { this.moved = false; return false; }
    this.drafts.remove(draftKey(from));   // the text goes on under the new path
    return true;
  }

  /** Stop timers. Unsaved text stays in the draft. */
  dispose() {
    this.disposed = true;
    clearTimeout(this.timer ?? undefined);
  }
}

export const draftStorageKey = draftKey;
