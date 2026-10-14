// @ts-nocheck
// Library data for notes: citable items (for [@ autocomplete and citation cards) and note
// names (for [[ autocomplete). No DOM here; ranking is unit-tested (tests/js/library.test.mjs).

const fold = (s) => String(s ?? '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();

export function shortAuthors(authors) {
  const fams = String(authors || '').split(';').map((a) => a.split(',')[0].trim()).filter(Boolean);
  if (!fams.length) return '';
  if (fams.length === 1) return fams[0];
  if (fams.length === 2) return `${fams[0]} & ${fams[1]}`;
  return `${fams[0]} et al.`;
}

/** Rank items for a [@ query. Every word must appear in key, authors, title or year. */
export function searchItems(items, query, limit = 30) {
  const words = fold(query).split(/[^\p{L}\p{N}_:.+\-/]+/u).filter(Boolean);
  const out = [];
  for (const it of items) {
    if (it.trashed) continue;
    const key = fold(it.key);
    const authors = fold(it.authors);
    const hay = `${key} ${authors} ${fold(it.title)} ${it.year ?? ''}`;
    if (!words.every((w) => hay.includes(w))) continue;
    const w = words[0] || '';
    let score = 5;
    if (!w) score = 4;
    else if (key.startsWith(w)) score = 0;
    else if (authors.split(';').some((a) => a.trim().startsWith(w))) score = 1;
    else if (key.includes(w)) score = 2;
    else if (fold(it.title).split(/\s+/).some((t) => t.startsWith(w))) score = 3;
    out.push([score, -(parseInt(it.year, 10) || 0), it.key, it]);
  }
  out.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2].localeCompare(b[2]));
  return out.slice(0, limit).map((x) => x[3]);
}

/**
 * Names to offer for [[ : the file name, or folder/name when two notes share a name
 * (that is how the backend resolves them, see index.resolve).
 */
export function noteChoices(notes) {
  const stemOf = (p) => p.split('/').pop().replace(/\.md$/i, '');
  const count = new Map();
  for (const n of notes) { const s = stemOf(n.path).toLowerCase(); count.set(s, (count.get(s) || 0) + 1); }
  return notes.map((n) => {
    const stem = stemOf(n.path);
    const insert = count.get(stem.toLowerCase()) > 1 ? n.path.replace(/\.md$/i, '') : stem;
    return { insert, stem, path: n.path, title: n.title || stem, folder: n.path.includes('/') ? n.path.slice(0, n.path.lastIndexOf('/')) : '' };
  });
}

export function searchNotes(choices, query, limit = 30) {
  const q = fold(query).trim();
  const out = [];
  for (const c of choices) {
    const stem = fold(c.stem), title = fold(c.title), path = fold(c.path);
    let score;
    if (!q) score = 3;
    else if (stem.startsWith(q)) score = 0;
    else if (title.startsWith(q)) score = 1;
    else if (stem.includes(q) || title.includes(q) || path.includes(q)) score = 2;
    else continue;
    out.push([score, c.path.split('/').length, c.stem.length, c.path, c]);
  }
  // same order as the backend's resolution: fewer folders first, then shorter, then by name
  out.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2] || a[3].localeCompare(b[3]));
  return out.slice(0, limit).map((x) => x[4]);
}

/**
 * What the text before the cursor asks for, if anything:
 *   {kind: 'cite', query, from, bracket}   "[see @smi" (bracket=true) or " @smi" (bracket=false)
 *   {kind: 'note', query, from}            "[[Rev" or "![[Rev"
 * `from` is the offset (within `before`) where the query starts.
 */
export function completionContext(before) {
  let m = /(!?)\[\[([^[\]\n|#]*)$/.exec(before);
  if (m) return { kind: 'note', query: m[2], from: before.length - m[2].length };
  m = /(^|[\s(;[])-?@([\p{L}\p{N}_:.+\-/]*)$/u.exec(before);
  if (!m) return null;
  const at = before.length - m[2].length;          // just after the @
  const open = before.lastIndexOf('[');
  const bracket = open >= 0 && !before.slice(open).includes(']') && before[open + 1] !== '[';
  return { kind: 'cite', query: m[2], from: at, bracket };
}
