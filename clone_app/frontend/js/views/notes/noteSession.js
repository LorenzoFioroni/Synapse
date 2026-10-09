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
// Status: 'saved' | 'dirty' | 'saving' | 'offline' | 'conflict'

const draftKey = (path) => `zc.noteDraft:${path}`;

export class NoteSession {
  /**
   * @param {object} o
   * @param {string} o.path
   * @param {string} o.text      text the editor starts with
   * @param {string} o.hash      server hash of o.serverText
   * @param {string} [o.serverText=o.text]
   * @param {{save(path,text,hash):Promise<{hash}>, create(path,text):Promise<{hash}>, get(path):Promise<{text,hash}>}} o.api
   * @param {{get(k), set(k,v), remove(k)}} o.drafts
   * @param {number} [o.delay=1500]
   * @param {(status, session) => void} [o.onStatus]
   * @param {(info:{mine, theirs, theirsHash, deleted}) => void} [o.onConflict]
   * @param {(text) => void} [o.onRemoteText]  replace the editor content
   * @param {(hash) => void} [o.onSaved]
   */
  constructor(o) {
    this.path = o.path;
    this.api = o.api;
    this.drafts = o.drafts;
    this.delay = o.delay ?? 1500;
    this.onStatus = o.onStatus || (() => {});
    this.onConflict = o.onConflict || (() => {});
    this.onRemoteText = o.onRemoteText || (() => {});
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
  static open(path, server, drafts) {
    const d = drafts.get(draftKey(path));
    if (!d || typeof d.text !== 'string') return { text: server.text, restored: false, conflict: false };
    if (d.text === server.text) { drafts.remove(draftKey(path)); return { text: server.text, restored: false, conflict: false }; }
    if (d.baseHash === server.hash) return { text: d.text, restored: true, conflict: false };
    return { text: d.text, restored: true, conflict: true };   // the file changed since the draft was made
  }

  _set(status) {
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
    clearTimeout(this.timer);
    this.timer = null;
    if (!this.conflict) this._set(this.inFlight && this.status === 'saving' ? 'saving' : 'saved');
  }

  _schedule(ms) {
    clearTimeout(this.timer);
    if (this.conflict || this.disposed) return;
    this.timer = setTimeout(() => { this.timer = null; this.save(); }, ms);
  }

  get dirty() { return this.text !== this.saved.text; }

  /** Call on every editor change. */
  update(text) {
    this.text = text;
    if (this.conflict) {   // keep the draft current, but never save
      this.drafts.set(draftKey(this.path), { text, baseHash: this.saved.hash, at: Date.now() });
      return;
    }
    if (text === this.saved.text) this._clean(); else this._dirty();
  }

  /** Save now if needed. Resolves when the server has the current text (or saving failed). */
  async save() {
    if (this.disposed && !this.dirty) return;
    if (this.conflict || !this.dirty) return;
    if (this.inFlight) { this.again = true; return this.inFlight; }
    clearTimeout(this.timer);
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
        if (e && e.status === 409) {
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
    let theirs = null, theirsHash = null, deleted = false;
    try {
      const r = await this.api.get(this.path);
      theirs = r.text; theirsHash = r.hash;
    } catch (e) {
      if (e && e.status === 404) deleted = true;
    }
    if (!deleted && theirs === this.text) {   // the other side wrote exactly our text
      this.saved = { text: theirs, hash: theirsHash };
      this.inFlight = null;
      this._clean();
      return;
    }
    clearTimeout(this.timer);
    this.conflict = { mine: this.text, theirs, theirsHash, deleted };
    this.drafts.set(draftKey(this.path), { text: this.text, baseHash: this.saved.hash, at: Date.now() });
    this._set('conflict');
    this.onConflict(this.conflict);
  }

  /** Enter the conflict state directly (used when a draft is older than the file). */
  async startConflict() { await this._enterConflict(); }

  /** 'mine' overwrites the file with the editor text; 'theirs' loads the file into the editor. */
  async resolve(choice) {
    const c = this.conflict;
    if (!c) return;
    this.conflict = null;
    if (choice === 'theirs' && !c.deleted) {
      this.saved = { text: c.theirs, hash: c.theirsHash };
      this.text = c.theirs;
      this.onRemoteText(c.theirs);
      this._clean();
      return;
    }
    if (c.deleted) {   // the file was deleted elsewhere: write it again
      const res = await this.api.create(this.path, this.text);
      this.saved = { text: this.text, hash: res.hash };
      this.onSaved(res.hash);
      this._clean();
      return;
    }
    // keep mine: base the next save on the file's current version
    this.saved = { text: c.theirs ?? '', hash: c.theirsHash };
    this._dirty();
    await this.save();
  }

  /** When the window regains focus: pick up edits made in another tab if we have none. */
  async checkRemote() {
    if (this.status !== 'saved' || this.inFlight) return;
    let r;
    try { r = await this.api.get(this.path); } catch { return; }
    if (this.status !== 'saved' || this.dirty || r.hash === this.saved.hash) return;
    this.saved = { text: r.text, hash: r.hash };
    this.text = r.text;
    this.onRemoteText(r.text);
    this.onSaved(r.hash);
  }

  /** Stop timers. Unsaved text stays in the draft. */
  dispose() {
    this.disposed = true;
    clearTimeout(this.timer);
  }
}

export const draftStorageKey = draftKey;
