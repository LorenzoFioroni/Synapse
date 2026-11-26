// Where each PDF was last read on this device, so opening it again (from the library, a note, a
// new window) goes back there. Kept in localStorage — it works offline and needs no server — for
// the most recently read PDFs only. An address with ?page=… (a cited page) still opens at that page.

import { storage, store } from '../util.js';
import type { ReadingPosition } from './linkNav.js';

/** A reading position, plus the horizontal scroll when the page is wider than the view. */
export type Position = ReadingPosition & { sx?: number };
type Book = Record<string, Position>;

const KEY = 'zc.viewer.lastRead';
const MAX = 300;

/** What names a viewer's PDF: its attachment or notes-PDF number (they survive renames), else its file. */
export function pdfKey(params: URLSearchParams): string | null {
  const att = params.get('att'), npdf = params.get('npdf'), file = params.get('file');
  return att ? `att:${att}` : npdf ? `npdf:${npdf}` : file ? `file:${file}` : null;
}

/** `book` with `key` at `pos`, as the most recent entry; the oldest beyond `max` are dropped. */
export function withPosition(book: Book, key: string, pos: Position, max = MAX): Book {
  const { [key]: _old, ...rest } = book;
  const keys = Object.keys(rest);
  const kept = keys.slice(Math.max(0, keys.length - (max - 1)));
  return { ...Object.fromEntries(kept.map((k) => [k, rest[k]])), [key]: pos };
}

/** Somewhere other than the very top of the first page (else there is nothing to go back to). */
export const worthReopening = (pos: Position | null | undefined): pos is Position =>
  !!pos && pos.page >= 1 && (pos.page > 1 || (pos.frac || 0) > 0.01);

export function saveLastRead(key: string, pos: Position): void {
  store(KEY, withPosition(storage<Book>(KEY, {}), key, pos));
}

/**
 * Where this viewer should open, when its address names no page: where this browser tab was on a
 * reload (its history entry), else where the PDF was last read. Null: at the top, as usual.
 */
export function startPosition(): Position | null {
  const params = new URLSearchParams(location.search);
  const key = pdfKey(params);
  if (!key || params.get('page')) return null;
  const pos = (history.state?.zcPos as Position | undefined) ?? storage<Book>(KEY, {})[key];
  return worthReopening(pos) ? pos : null;
}
