// JavaScript port of backend/notes/parser.py, used ONLY for display: the live preview and the
// read view use it to know which characters are math, links or citations.
//
// The backend stays the single source of truth for the link graph (what a note links to).
// tests/notes/test_scanner_parity.py runs this module and the Python parser on every fixture
// and requires the same mask and the same links, so the two can never drift apart silently.
//
// Positions are offsets into the text as given (after CRLF → LF), in UTF-16 code units.

const KEY = '[A-Za-z0-9_][A-Za-z0-9_:.+\\-/]*';
const CITE_PART = new RegExp(`^((?:[\\s\\S]*?[\\s(])?)(-?)@(${KEY})(?:\\s*,\\s*([\\s\\S]*?))?\\s*$`, 'u');
const PAGE = /^(?:p|pp|page|pages|pg)\.?\s*(\d+)/i;
// [@key, p. 4, att:12]: the PDF (attachment id) a citation means (attachment_of in parser.py).
const ATT = /(?:^|[\s,])att:(\d+)(?=$|[\s,])/i;
const FENCE = /^([ \t]*)(`{3,}|~{3,})(.*)$/;
const HEADING = /^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*(?:#+[ \t]*)?$/;
const WIKI = /(!?)\[\[([^[\]\n]+?)\]\]/g;
const BRACKET = /(?<![![\]\\])\[([^[\]\n]*@[^[\]\n]*)\](?![([\]])/g;
const IMAGE = /!\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/gd;
const TAG = /(?<![\p{L}\p{N}_/&#\])([])#([A-Za-z][\p{L}\p{N}_\-/]*)/gu;
const DIGITS = '0123456789';

/** A region the scanner blanked (code, math, comments, fenced blocks). */
export interface Span {
  kind: 'code' | 'math' | 'comment' | 'fence' | 'mathblock';
  from: number;
  to: number;
  display?: boolean;
  open?: boolean;          // a fence or math block that is never closed
}

/** A link, embed, citation or image, as backend/notes/parser.py reports it. */
export interface ScanLink {
  kind: 'note' | 'embed' | 'cite' | 'image';
  from: number;
  to: number;
  target: string;
  line: number;
  heading: string;
  alias: string;
  locator: string;
  page: number | null;
  prefix: string;
  suppress_author: boolean;
  group: number;           // citations in the same [ … ] share it
  width?: number | null;   // images: ![alt|300](…)
}

export interface Heading { level: number; text: string; line: number; from: number }
export interface TagHit { tag: string; from: number; to: number }

export interface Scan {
  bodyStart: number;
  masked: string;
  spans: Span[];
  links: ScanLink[];
  headings: Heading[];
  tags: TagHit[];
}

/** One citation inside [ … ]: [see @key, p. 4] → {prefix: 'see', key, locator: 'p. 4', page: 4}. */
export interface Cite { key: string; prefix: string; suppress: boolean; locator: string; page: number | null }

/** Python str.strip() for the whitespace that matters here. */
const strip = (s: string) => s.replace(/^\s+|\s+$/gu, '');

/** Mirror of frontmatter.split(): returns {header, bodyStart (offset), bodyLine (0-based)}. */
export function splitFrontmatter(text: string): { header: string | null; bodyStart: number; bodyLine: number } {
  if (!text.startsWith('---\n')) return { header: null, bodyStart: 0, bodyLine: 0 };
  let pos = 4, line = 1;
  while (pos <= text.length) {
    const nl = text.indexOf('\n', pos);
    const end = nl < 0 ? text.length : nl;
    const l = text.slice(pos, end);
    if (l === '---' || l === '...') {
      return { header: text.slice(4, pos), bodyStart: nl < 0 ? text.length : nl + 1, bodyLine: line + 1 };
    }
    if (nl < 0) break;
    pos = nl + 1;
    line++;
  }
  return { header: null, bodyStart: 0, bodyLine: 0 };
}

function blank(chars: string[], a: number, b: number): void {
  for (let i = a; i < b; i++) if (chars[i] !== '\n') chars[i] = ' ';
}

function maskInline(text: string, chars: string[], start: number, end: number, spans: Span[]): void {
  let i = start;
  while (i < end) {
    const ch = text[i];
    if (ch === '\\' && i + 1 < end && text[i + 1] !== '\n') { blank(chars, i, i + 2); i += 2; continue; }
    if (ch === '`') {
      let j = i;
      while (j < end && text[j] === '`') j++;
      const run = j - i;
      let k = j, close = -1;
      while (k < end) {
        if (text[k] === '`') {
          let m = k;
          while (m < end && text[m] === '`') m++;
          if (m - k === run) { close = k; break; }
          k = m;
        } else k++;
      }
      if (close >= 0) { spans.push({ kind: 'code', from: i, to: close + run }); blank(chars, i, close + run); i = close + run; }
      else i = j;
      continue;
    }
    if (ch === '$') {
      if (text.startsWith('$$', i)) {
        const close = text.indexOf('$$', i + 2);
        if (close >= 0 && close + 2 <= end) {
          spans.push({ kind: 'math', display: true, from: i, to: close + 2 });
          blank(chars, i, close + 2); i = close + 2; continue;
        }
        i += 2; continue;
      }
      if (i + 1 < end && !' \t\n$'.includes(text[i + 1])) {
        const eol = text.indexOf('\n', i);
        const lineEnd = eol < 0 || eol >= end ? end : eol;
        let k = i + 1;
        while (k < lineEnd) {
          if (text[k] === '\\') { k += 2; continue; }
          if (text[k] === '$' && !' \t\n'.includes(text[k - 1]) && !(k + 1 < text.length && DIGITS.includes(text[k + 1]))) break;
          k++;
        }
        if (k < lineEnd) {
          spans.push({ kind: 'math', display: false, from: i, to: k + 1 });
          blank(chars, i, k + 1); i = k + 1; continue;
        }
      }
      i++; continue;
    }
    if (text.startsWith('<!--', i)) {
      const close = text.indexOf('-->', i + 4);
      const stop = close >= 0 ? close + 3 : text.length;
      spans.push({ kind: 'comment', from: i, to: stop });
      blank(chars, i, stop); i = stop; continue;
    }
    i++;
  }
}

/**
 * Mirror of parser.mask(body). Returns {masked, spans} where spans lists the regions that were
 * blanked: {kind: 'code'|'math'|'comment'|'fence'|'mathblock', from, to, display?}.
 */
export function mask(body: string): { masked: string; spans: Span[] } {
  const units = body.split('');   // UTF-16 units, so offsets match CodeMirror
  const spans: Span[] = [];
  const lines = body.split('\n');
  const offsets: number[] = [];
  let pos = 0;
  for (const l of lines) { offsets.push(pos); pos += l.length + 1; }
  let paraStart: number | null = null, fence: [string, number] | null = null, fenceStart = 0, mathBlock = false, mathStart = 0;
  const flush = (upto: number) => { if (paraStart !== null) { maskInline(body, units, paraStart, upto, spans); paraStart = null; } };
  lines.forEach((line, n) => {
    const start = offsets[n], stop = start + line.length;
    if (fence) {
      blank(units, start, stop);
      const m = FENCE.exec(line);
      if (m && m[2][0] === fence[0] && m[2].length >= fence[1] && !strip(m[3])) {
        fence = null;
        spans.push({ kind: 'fence', from: fenceStart, to: stop });
      }
      return;
    }
    if (mathBlock) {
      blank(units, start, stop);
      if (strip(line).endsWith('$$')) { mathBlock = false; spans.push({ kind: 'mathblock', display: true, from: mathStart, to: stop }); }
      return;
    }
    const m = FENCE.exec(line);
    if (m && !(m[2][0] === '`' && m[3].includes('`'))) {
      flush(start);
      fence = [m[2][0], m[2].length];
      fenceStart = start;
      blank(units, start, stop);
      return;
    }
    const s = strip(line);
    if (s.startsWith('$$') && (s === '$$' || !s.slice(2).replace(/\s+$/u, '').endsWith('$$'))) {
      flush(start);
      mathBlock = true;
      mathStart = start;
      blank(units, start, stop);
      return;
    }
    if (!s) { flush(start); return; }
    if (paraStart === null) paraStart = start;
  });
  flush(body.length);
  if (fence) spans.push({ kind: 'fence', from: fenceStart, to: body.length, open: true });
  if (mathBlock) spans.push({ kind: 'mathblock', display: true, from: mathStart, to: body.length, open: true });
  return { masked: units.join(''), spans };
}

/**
 * `[label]:` at the start of a line (at most three spaces in) defines a Markdown link reference.
 * Anywhere else a colon after the brackets is punctuation: "as in [@key]:". Also used by the
 * renderer (render.ts).
 */
export function referenceDefinition(text: string, start: number, end: number): boolean {
  if (text[end] !== ':') return false;
  const lineStart = text.lastIndexOf('\n', start - 1) + 1;
  return start - lineStart <= 3 && !text.slice(lineStart, start).replace(/ /g, '');
}

/** The citations inside [ … ] (split on ';'), or null when any part is not a citation. Also used by
 *  the renderer (render.ts), so the editor, read mode and the backend agree on what a citation is. */
export function parseCites(inner: string): Cite[] | null {
  const out: Cite[] = [];
  for (const part of inner.split(';')) {
    const m = CITE_PART.exec(strip(part));
    if (!m) return null;
    const key = m[3].replace(/[.:/-]+$/, '');
    if (!key) return null;
    const loc = strip(m[4] || '');
    const pm = PAGE.exec(loc);
    out.push({ key, prefix: strip(m[1]), suppress: m[2] === '-', locator: loc, page: pm ? Number(pm[1]) : null });
  }
  return out;
}

/** [[target#heading|alias]] split into its parts (an escaped \\| also starts the alias). */
export function splitWiki(inner: string): { target: string; heading: string; alias: string } {
  inner = inner.replaceAll('\\|', '|');
  const bar = inner.indexOf('|');
  let target = bar >= 0 ? inner.slice(0, bar) : inner;
  const alias = bar >= 0 ? inner.slice(bar + 1) : '';
  const hash = target.indexOf('#');
  const heading = hash >= 0 ? target.slice(hash + 1) : '';
  if (hash >= 0) target = target.slice(0, hash);
  return { target: strip(target), heading: strip(heading), alias: strip(alias) };
}

/** The attachment id a citation names with att:<id> in its locator, or null. */
export const attachmentOf = (locator: string | null | undefined): number | null => { const m = ATT.exec(locator || ''); return m ? Number(m[1]) : null; };

/**
 * Scan a whole note. Offsets in the result are relative to `text` (front-matter included).
 */
export function scan(text: string): Scan {
  text = text.replace(/\r\n?/g, '\n');
  const fm = splitFrontmatter(text);
  const body = text.slice(fm.bodyStart);
  const off = fm.bodyStart;
  const { masked, spans } = mask(body);
  const lineStarts = [0];
  for (let i = 0; i < masked.length; i++) if (masked[i] === '\n') lineStarts.push(i + 1);
  const lineOf = (o: number) => {
    let lo = 0, hi = lineStarts.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (lineStarts[mid] <= o) lo = mid; else hi = mid - 1; }
    return lo + 1 + fm.bodyLine;
  };
  const found: [number, ScanLink][] = [];
  for (const m of masked.matchAll(WIKI)) {
    const at = m.index!;
    const inner = body.slice(at + m[1].length + 2, at + m[0].length - 2);
    const w = splitWiki(inner);
    if (!w.target && !w.heading) continue;
    found.push([at, { kind: m[1] ? 'embed' : 'note', from: at + off, to: at + m[0].length + off,
      target: w.target, heading: w.heading, alias: w.alias, locator: '', page: null, prefix: '', suppress_author: false,
      group: 0, line: lineOf(at) }]);
  }
  let group = 0;
  for (const m of masked.matchAll(BRACKET)) {
    const at = m.index!;
    const cites = parseCites(body.slice(at + 1, at + m[0].length - 1));
    if (!cites || referenceDefinition(masked, at, at + m[0].length)) continue;
    group++;
    for (const c of cites) {
      found.push([at, { kind: 'cite', from: at + off, to: at + m[0].length + off, target: c.key,
        heading: '', alias: '', locator: c.locator, page: c.page, prefix: c.prefix, suppress_author: c.suppress,
        group, line: lineOf(at) }]);
    }
  }
  for (const m of masked.matchAll(IMAGE)) {
    const at = m.index!;
    const alt = m[1];
    found.push([at, { kind: 'image', from: at + off, to: at + m[0].length + off,
      target: m[2], heading: '', alias: strip(alt.split('|')[0]),
      width: Number((alt.split('|')[1] || '').trim()) || null,
      locator: '', page: null, prefix: '', suppress_author: false, group: 0, line: lineOf(at) }]);
  }
  found.sort((a, b) => a[0] - b[0]);

  const headings: Heading[] = [];
  const tags: TagHit[] = [];
  const bodyLines = body.split('\n');
  masked.split('\n').forEach((ml, idx) => {
    const hm = HEADING.exec(ml);
    if (hm) {
      const raw = HEADING.exec(bodyLines[idx]) || hm;
      headings.push({ level: hm[1].length, text: strip(raw[2]), line: idx + 1 + fm.bodyLine, from: lineStarts[idx] + off });
    }
    for (const tm of ml.matchAll(TAG)) {
      const t = tm[1].replace(/[/-]+$/, '');
      if (t && !/^\d+$/.test(t)) tags.push({ tag: t, from: lineStarts[idx] + tm.index! + off, to: lineStarts[idx] + tm.index! + tm[0].length + off });
    }
  });
  return {
    bodyStart: off,
    masked,
    spans: spans.map((s) => ({ ...s, from: s.from + off, to: s.to + off })),
    links: found.map(([, l]) => l),
    headings,
    tags,
  };
}
