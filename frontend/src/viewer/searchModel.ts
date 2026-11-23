// PDF text search, independent of rendered pages. Offsets stay in the original UTF-16 text
// items so a match can be highlighted without rewriting pdf.js's selectable text layer.
export interface SearchItem { str: string; hasEOL?: boolean }
export interface TextPosition { item: number; offset: number }
export interface SearchMatch { page: number; begin: TextPosition; end: TextPosition }
export interface SearchOptions { matchCase: boolean; wholeWord: boolean }
interface Normalized { text: string; starts: number[]; ends: number[] }
export interface SearchPage extends Normalized { runs: { start: number; end: number }[] }

function normalize(raw: string): Normalized {
  let text = '';
  const starts: number[] = [], ends: number[] = [];
  for (let i = 0; i < raw.length;) {
    const ch = String.fromCodePoint(raw.codePointAt(i)!);
    const end = i + ch.length;
    // A printed line-end hyphen is not part of the word; an ordinary in-line hyphen is.
    if ((ch === '-' || ch === '\u2010') && /\p{L}$/u.test(raw.slice(0, i))) {
      const wrap = /^[ \t]*\n\s*(?=\p{L})/u.exec(raw.slice(end));
      if (wrap) { i = end + wrap[0].length; continue; }
    }
    if (ch === '\u00ad') { i = end; continue; }
    const plain = ch.normalize('NFKD').replace(/\p{M}/gu, '');
    for (const c of plain) {
      if (/\s/u.test(c)) {
        if (text.endsWith(' ')) ends[ends.length - 1] = end;
        else { text += ' '; starts.push(i); ends.push(end); }
      } else {
        text += c;
        for (let k = 0; k < c.length; k++) { starts.push(i); ends.push(end); }
      }
    }
    i = end;
  }
  return { text, starts, ends };
}

export function indexPage(items: SearchItem[]): SearchPage {
  let raw = '';
  const runs = items.map((item) => {
    const start = raw.length;
    raw += item.str;
    const end = raw.length;
    if (item.hasEOL) raw += '\n';
    return { start, end };
  });
  return { ...normalize(raw), runs };
}

function position(page: SearchPage, at: number, end: boolean): TextPosition {
  // End offsets belong to the preceding run when they fall on an item boundary.
  let lo = 0, hi = page.runs.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (page.runs[mid].end <= at - (end ? 1 : 0)) lo = mid + 1;
    else hi = mid;
  }
  return { item: lo, offset: at - page.runs[lo].start };
}

export function pageMatches(page: SearchPage, pageNumber: number, query: string, options: SearchOptions): SearchMatch[] {
  let needle = normalize(query).text.trim(), text = page.text;
  if (!needle) return [];
  if (!options.matchCase) { needle = needle.toLowerCase(); text = text.toLowerCase(); }
  const matches: SearchMatch[] = [];
  for (let at = text.indexOf(needle); at >= 0; at = text.indexOf(needle, at + needle.length)) {
    const end = at + needle.length;
    if (options.wholeWord && (/(?:\p{L}|\p{N}|_)$/u.test(text.slice(Math.max(0, at - 2), at)) ||
      /^(?:\p{L}|\p{N}|_)/u.test(text.slice(end, end + 2)))) continue;
    matches.push({ page: pageNumber, begin: position(page, page.starts[at], false), end: position(page, page.ends[end - 1], true) });
  }
  return matches;
}

/** Extract one page at a time, cache text only, and stop issuing work when superseded or closed. */
export class PdfSearchIndex {
  private cache = new Map<number, Promise<SearchPage>>();
  constructor(readonly count: number, private read: (page: number) => Promise<SearchItem[]>) {}

  private page(num: number): Promise<SearchPage> {
    let pending = this.cache.get(num);
    if (!pending) {
      pending = this.read(num).then(indexPage).catch((error) => { this.cache.delete(num); throw error; });
      this.cache.set(num, pending);
    }
    return pending;
  }

  async search(query: string, options: SearchOptions, start: number, cancelled: () => boolean,
    found: (page: number, matches: SearchMatch[], failed: boolean) => void): Promise<void> {
    for (let i = 0; i < this.count; i++) {
      if (cancelled()) return;
      const num = (Math.max(1, Math.min(this.count, start)) - 1 + i) % this.count + 1;
      try {
        const page = await this.page(num);
        if (cancelled()) return;
        found(num, pageMatches(page, num, query, options), false);
      } catch {
        if (cancelled()) return;
        found(num, [], true);
      }
      // A cached long document must still let the next keystroke or Close cancel the scan.
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
  }
}
