// Equation numbers of a note (issue #43), shared by the read view and the live preview.
//
// KaTeX renders each formula on its own, so it cannot number equations through a note: a numbered
// environment restarts at (1) in every block, and \label / \eqref are unknown to it. This module
// looks at all display equations of a note in order and rewrites their TeX before KaTeX sees it:
//
//  - numbered environments (equation, align, gather, alignat, flalign) get consecutive
//    numbers, row by row for the multi-row ones, as in LaTeX; starred ones get none;
//  - with mode "all", every other display equation is numbered too ($$ … $$);
//  - \tag{x} shows x and does not count; \notag / \nonumber leave a row unnumbered;
//  - \label{name} is removed and remembered; \eqref{name} / \ref{name} anywhere in the note
//    (in math or in the text) become the number.
//
// Numbers are written as explicit \tag{n}, so KaTeX never uses its own page-wide counter.
// The module is DOM-free (tested with Node).

const NUMBERED = new Set(['equation', 'align', 'gather', 'alignat', 'flalign']);   // (KaTeX has no multline)
const MULTIROW = new Set(['align', 'gather', 'alignat', 'flalign']);
export const MODES = ['latex', 'all'];

const LABEL = /\\label\s*\{([^{}]*)\}/g;
const NOTAG = /\\(?:notag|nonumber)(?![A-Za-z])/;
const TAG = /\\tag(\*?)\s*\{((?:[^{}]|\{[^{}]*\})*)\}/;
const REF = /\\(eqref|ref)\s*\{([^{}]*)\}/g;

/** The rows of an environment body: split at top-level \\ (not inside braces or nested environments). */
export function splitRows(body) {
  const rows = [];
  let depth = 0, start = 0;
  for (let i = 0; i < body.length; i++) {
    const c = body[i];
    if (c === '\\') {
      if (body.startsWith('\\begin', i)) { depth++; i += 5; continue; }
      if (body.startsWith('\\end', i)) { depth--; i += 3; continue; }
      if (body[i + 1] === '\\' && depth === 0) { rows.push(body.slice(start, i)); start = i + 2; i++; continue; }
      i++;   // \{ \} \\ inside: skip the escaped character
      continue;
    }
    if (c === '{') depth++;
    else if (c === '}') depth--;
  }
  rows.push(body.slice(start));
  return rows;
}

/** The single top-level environment a formula consists of, or null. */
function outerEnv(tex) {
  const m = /^\s*\\begin\s*\{([A-Za-z]+)(\*?)\}([\s\S]*)\\end\s*\{\1\2\}\s*$/.exec(tex);
  if (!m) return null;
  // a formula like "\begin{a}…\end{a} + \begin{a}…\end{a}" is not one environment
  const inner = m[3];
  let depth = 0;
  for (const t of inner.matchAll(/\\(begin|end)\s*\{([A-Za-z]+\*?)\}/g)) {
    depth += t[1] === 'begin' ? 1 : -1;
    if (depth < 0) return null;
  }
  return { name: m[1], starred: m[2] === '*', body: inner };
}

/** What a row (or a whole formula) shows: an explicit tag, nothing, or an automatic number. */
function rowKind(text, auto) {
  const t = TAG.exec(text);
  if (t) return { tag: t[2], star: t[1] === '*' };
  if (NOTAG.test(text)) return { none: true };
  return auto ? { auto: true } : { none: true };
}

const stripLabels = (s, found) => s.replace(LABEL, (_, name) => { found.push(name.trim()); return ''; });

/**
 * Number the display equations of a note.
 * @param {string[]} formulas  TeX of each display equation, in document order (without $$)
 * @param {{mode?: 'latex'|'all'}} [o]
 * @returns {{formulas: string[], labels: Map<string, string|null>, numbers: string[][], names: string[][]}}
 *   formulas: the TeX to give KaTeX (labels removed, numbers as \tag, references not yet resolved);
 *   labels: label → shown number (e.g. "3" or "A.1"; null if that equation has none);
 *   numbers: the numbers each formula shows;  names: the labels each formula defines.
 */
export function numberEquations(formulas, { mode = 'latex' } = {}) {
  const labels = new Map();
  const numbers = [], names = [];
  let counter = 0;
  const out = formulas.map((tex) => {
    const shown = [], mine = [];
    names.push(mine);
    const doRow = (row, auto) => {
      const found = [];
      let text = stripLabels(row, found);
      const kind = rowKind(text, auto);
      let num = null;
      if (kind.tag != null) num = kind.tag;
      else if (kind.auto) {
        num = String(++counter);
        // the number goes at the end of the row, before trailing white space
        const m = /\s*$/.exec(text);
        text = `${text.slice(0, m.index)} \\tag{${num}}${text.slice(m.index)}`;
      }
      if (num != null) shown.push(num);
      for (const n of found) if (n && !labels.has(n)) { labels.set(n, num); mine.push(n); }
      return text;
    };
    const env = outerEnv(tex);
    if (env && MULTIROW.has(env.name)) {
      const rows = splitRows(env.body);
      // a final empty row (a trailing \\) is no equation
      const body = rows.map((r, i) => (i === rows.length - 1 && !r.trim() ? r : doRow(r, !env.starred))).join('\\\\');
      numbers.push(shown);
      return tex.replace(env.body, () => body);
    }
    if (env && NUMBERED.has(env.name)) {
      const body = doRow(env.body, !env.starred);
      numbers.push(shown);
      return tex.replace(env.body, () => body);
    }
    // anything else is one equation: numbered with mode "all" (or by its own \tag)
    const res = doRow(tex, mode === 'all' && !(env && env.starred));
    numbers.push(shown);
    // KaTeX knows \notag only inside environments: drop it elsewhere
    return env ? res : res.replace(new RegExp(NOTAG.source, 'g'), '');
  });
  return { formulas: out, labels, numbers, names };
}

/** The text \eqref{name} / \ref{name} stand for: "(3)" / "3", or null for an unknown label. */
export function refText(kind, name, labels) {
  const n = labels.get(String(name).trim());
  if (n == null) return null;
  return kind === 'eqref' ? `(${n})` : n;
}

/** TeX with \eqref / \ref replaced by the numbers (unknown labels in red as (??)). */
export function resolveRefs(tex, labels) {
  if (!tex.includes('ref')) return tex;
  return tex.replace(REF, (_, kind, name) => {
    const t = refText(kind, name, labels);
    return t == null ? '\\textcolor{#cc0000}{\\text{(??)}}' : `\\text{${t.replace(/[{}\\]/g, '')}}`;
  });
}

/** \eqref{…} / \ref{…} written in the text (outside math): {from, to, kind, name}. */
export function textRefs(text) {
  const out = [];
  for (const m of text.matchAll(/\\(eqref|ref)\{([^{}\n]*)\}/g)) out.push({ from: m.index, to: m.index + m[0].length, kind: m[1], name: m[2].trim() });
  return out;
}

/** An id for the anchor of a label (read view). */
export const labelId = (name) => `eq-${String(name).trim().replace(/[^\p{L}\p{N}_-]+/gu, '-')}`;

/**
 * Lay out KaTeX's display HTML so the number never overlaps the formula: the formula is centred
 * between the number and an invisible copy of it; when space runs out the copy shrinks first and
 * then the line scrolls, the number staying after the formula (as LaTeX moves it).
 */
export function layoutTag(html) {
  const open = '<span class="katex-html" aria-hidden="true">';
  const i = html.indexOf(open);
  const t = html.lastIndexOf('<span class="katex-tag">');
  if (i < 0 || t < i) return html;
  const tail = '</span></span></span>';   // closes katex-html, katex, katex-display
  if (!html.endsWith(tail)) return html;
  const tag = html.slice(t, html.length - tail.length);
  const start = i + open.length;
  const ghost = tag.replace('<span class="katex-tag">', '<span class="katex-tag eq-ghost" aria-hidden="true">');
  return `${html.slice(0, i)}<span class="katex-html eq-tagged" aria-hidden="true">${ghost}<span class="eq-main">${html.slice(start, t)}</span>${tag}${tail}`;
}
