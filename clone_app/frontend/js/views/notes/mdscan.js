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
const FENCE = /^([ \t]*)(`{3,}|~{3,})(.*)$/;
const HEADING = /^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*(?:#+[ \t]*)?$/;
const WIKI = /(!?)\[\[([^[\]\n]+?)\]\]/g;
const BRACKET = /(?<![![\]\\])\[([^[\]\n]*@[^[\]\n]*)\](?![([\]:])/g;
const IMAGE = /!\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/gd;
const TAG = /(?<![\p{L}\p{N}_/&#\])([])#([A-Za-z][\p{L}\p{N}_\-/]*)/gu;
const DIGITS = '0123456789';

/** Python str.strip() for the whitespace that matters here. */
const strip = (s) => s.replace(/^\s+|\s+$/gu, '');

/** Mirror of frontmatter.split(): returns {header, bodyStart (offset), bodyLine (0-based)}. */
export function splitFrontmatter(text) {
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

function blank(chars, a, b) {
  for (let i = a; i < b; i++) if (chars[i] !== '\n') chars[i] = ' ';
}

function maskInline(text, chars, start, end, spans) {
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
export function mask(body) {
  const units = body.split('');   // UTF-16 units, so offsets match CodeMirror
  const spans = [];
  const lines = body.split('\n');
  const offsets = [];
  let pos = 0;
  for (const l of lines) { offsets.push(pos); pos += l.length + 1; }
  let paraStart = null, fence = null, fenceStart = 0, mathBlock = false, mathStart = 0;
  const flush = (upto) => { if (paraStart !== null) { maskInline(body, units, paraStart, upto, spans); paraStart = null; } };
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

function parseCiteGroup(inner) {
  const out = [];
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

function splitWiki(inner) {
  inner = inner.replaceAll('\\|', '|');
  const bar = inner.indexOf('|');
  let target = bar >= 0 ? inner.slice(0, bar) : inner;
  const alias = bar >= 0 ? inner.slice(bar + 1) : '';
  const hash = target.indexOf('#');
  const heading = hash >= 0 ? target.slice(hash + 1) : '';
  if (hash >= 0) target = target.slice(0, hash);
  return { target: strip(target), heading: strip(heading), alias: strip(alias) };
}

/**
 * Scan a whole note. Offsets in the result are relative to `text` (front-matter included).
 * Returns {bodyStart, masked, spans, links, headings, tags}.
 *   links: {kind: 'note'|'embed'|'cite'|'image', from, to, target, line, heading, alias,
 *           locator, page, prefix, suppress_author, group}
 */
export function scan(text) {
  text = text.replace(/\r\n?/g, '\n');
  const fm = splitFrontmatter(text);
  const body = text.slice(fm.bodyStart);
  const off = fm.bodyStart;
  const { masked, spans } = mask(body);
  const lineStarts = [0];
  for (let i = 0; i < masked.length; i++) if (masked[i] === '\n') lineStarts.push(i + 1);
  const lineOf = (o) => {
    let lo = 0, hi = lineStarts.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (lineStarts[mid] <= o) lo = mid; else hi = mid - 1; }
    return lo + 1 + fm.bodyLine;
  };
  const found = [];
  for (const m of masked.matchAll(WIKI)) {
    const inner = body.slice(m.index + m[1].length + 2, m.index + m[0].length - 2);
    const w = splitWiki(inner);
    if (!w.target && !w.heading) continue;
    found.push([m.index, { kind: m[1] ? 'embed' : 'note', from: m.index + off, to: m.index + m[0].length + off,
      target: w.target, heading: w.heading, alias: w.alias, locator: '', page: null, prefix: '', suppress_author: false,
      group: 0, line: lineOf(m.index) }]);
  }
  let group = 0;
  for (const m of masked.matchAll(BRACKET)) {
    const cites = parseCiteGroup(body.slice(m.index + 1, m.index + m[0].length - 1));
    if (!cites) continue;
    group++;
    for (const c of cites) {
      found.push([m.index, { kind: 'cite', from: m.index + off, to: m.index + m[0].length + off, target: c.key,
        heading: '', alias: '', locator: c.locator, page: c.page, prefix: c.prefix, suppress_author: c.suppress,
        group, line: lineOf(m.index) }]);
    }
  }
  for (const m of masked.matchAll(IMAGE)) {
    const alt = m[1];
    found.push([m.index, { kind: 'image', from: m.index + off, to: m.index + m[0].length + off,
      target: m[2], heading: '', alias: strip(alt.split('|')[0]),
      width: Number((alt.split('|')[1] || '').trim()) || null,
      locator: '', page: null, prefix: '', suppress_author: false, group: 0, line: lineOf(m.index) }]);
  }
  found.sort((a, b) => a[0] - b[0]);

  const headings = [];
  const tags = [];
  const bodyLines = body.split('\n');
  masked.split('\n').forEach((ml, idx) => {
    const hm = HEADING.exec(ml);
    if (hm) {
      const raw = HEADING.exec(bodyLines[idx]) || hm;
      headings.push({ level: hm[1].length, text: strip(raw[2]), line: idx + 1 + fm.bodyLine, from: lineStarts[idx] + off });
    }
    for (const tm of ml.matchAll(TAG)) {
      const t = tm[1].replace(/[/-]+$/, '');
      if (t && !/^\d+$/.test(t)) tags.push({ tag: t, from: lineStarts[idx] + tm.index + off, to: lineStarts[idx] + tm.index + tm[0].length + off });
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
