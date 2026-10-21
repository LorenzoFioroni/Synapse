// Back/Forward between the places jumped to in a PDF, kept in memory. A viewer in a tab of its own
// uses the browser's history instead (links.ts); a viewer inside one of the app's tabs cannot,
// because the page around it shares that history: the app's Back would step through the PDF's
// jumps out of sight, and the PDF's Back could leave the app's current place.

export interface Jump<P> {
  pos: P | null;
  /** The viewer's address at this place (a reload reopens there). */
  url?: string;
}

export class JumpHistory<P> {
  entries: Jump<P>[] = [{ pos: null }];
  idx = 0;

  get canBack(): boolean { return this.idx > 0; }
  get canForward(): boolean { return this.idx < this.entries.length - 1; }

  /** Where the reader is now, at the current place (updated while scrolling). */
  record(pos: P | null): void {
    if (pos) this.entries[this.idx] = { ...this.entries[this.idx], pos };
  }

  /** A jump to a new place: the places ahead of the current one are dropped, as in a browser. */
  push(pos: P | null, url?: string): void {
    this.entries.splice(this.idx + 1);
    this.entries.push({ pos, url });
    this.idx = this.entries.length - 1;
  }

  back(): Jump<P> | null {
    if (!this.canBack) return null;
    return this.entries[--this.idx];
  }

  forward(): Jump<P> | null {
    if (!this.canForward) return null;
    return this.entries[++this.idx];
  }
}
