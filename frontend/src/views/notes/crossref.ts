// References to equations and figures, as cleveref writes them: \cref{name} knows whether the
// label is an equation's or a figure's and writes the name with the number.
//
//   \ref{fig:a}            2                 \eqref{eq:x}        (3)
//   \cref{fig:a}           Fig. 2            \cref{eq:x}         Eq. (3)
//   \Cref{fig:a}           Figure 2          \Cref{eq:x}         Equation (3)    (start of a sentence)
//   \cref{fig:a,fig:b}     Figs. 1 and 2     \cref{eq:x,eq:y,eq:z}   Eqs. (1)–(3)
//   \cref{fig:a,eq:x}      Fig. 2 and Eq. (3)
//   a panel of a gallery   Fig. 2(b)
//
// Numbers are sorted within a kind and three or more in a row become a range; names a note does
// not define show as ?? (in red). Equations win a name both use. No DOM here
// (tests/js/crossref.test.mjs); the renderer, the live preview and the Markdown export use it.

export type RefKind = 'ref' | 'eqref' | 'cref' | 'Cref';
export type TargetType = 'eq' | 'fig' | 'sec';

/** What a label stands for: an equation's or a figure's number ("3", "2(b)"), or null when unknown. */
export type Lookup = (name: string) => { type: TargetType; num: string } | null;

/** The labels of a note's equations (null: labelled but not numbered) and of its figures. */
export function lookupIn(eqLabels: Map<string, string | null> | undefined, figLabels: Map<string, string> | undefined,
  sectionLabels?: Map<string, string>): Lookup {
  return (name) => {
    const n = name.trim();
    if (eqLabels?.has(n)) { const num = eqLabels.get(n); return num == null ? null : { type: 'eq', num }; }
    const f = figLabels?.get(n);
    if (f != null) return { type: 'fig', num: f };
    const s = sectionLabels?.get(n);
    return s == null ? null : { type: 'sec', num: s };
  };
}

/** A piece of a written reference: text, or a number that links to its label. */
export type RefPart = string | { name: string; text: string; missing?: boolean };

const WORDS: Record<TargetType, [string, string, string, string]> = {
  eq: ['Eq.', 'Eqs.', 'Equation', 'Equations'],
  fig: ['Fig.', 'Figs.', 'Figure', 'Figures'],
  sec: ['Sec.', 'Secs.', 'Section', 'Sections'],
};

/** The names a reference holds: \cref takes a comma-separated list, the others one name. */
export const refNames = (kind: string, inner: string): string[] => (kind === 'cref' || kind === 'Cref'
  ? inner.split(',').map((s) => s.trim()).filter(Boolean) : [inner.trim()]);

/** "a", "a and b", "a, b and c" (as cleveref joins them). */
function joinList<T>(items: T[][], sep: T[], and: T[]): T[] {
  const out: T[] = [];
  items.forEach((it, i) => { if (i) out.push(...(i === items.length - 1 ? and : sep)); out.push(...it); });
  return out;
}

/** A reference written out, as parts (numbers separately, so they can be links). */
export function refParts(kind: RefKind, names: string[], lookup: Lookup): RefPart[] {
  if (kind === 'ref' || kind === 'eqref') {
    const name = names[0] || '';
    const t = lookup(name);
    if (!t) return [{ name, text: '(??)', missing: true }];
    return [{ name, text: kind === 'eqref' ? `(${t.num})` : t.num }];
  }
  const full = kind === 'Cref';
  const groups = new Map<TargetType | 'missing', { name: string; num: string }[]>();
  for (const name of names) {
    const t = lookup(name);
    const key = t ? t.type : 'missing';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push({ name, num: t ? t.num : '??' });
  }
  const sections: RefPart[][] = [];
  for (const [type, refs] of groups) {
    if (type === 'missing') {
      sections.push(joinList<RefPart>(refs.map((r) => [{ name: r.name, text: '??', missing: true }]), [', '], [' and ']));
      continue;
    }
    const w = WORDS[type];
    const wrap = (n: string) => (type === 'eq' ? `(${n})` : n);
    // sort by number, drop repeats, and make runs of three or more consecutive numbers a range
    const seen = new Set<string>();
    const sorted = refs.filter((r) => !seen.has(r.num) && seen.add(r.num))
      .sort((a, b) => (parseFloat(a.num) - parseFloat(b.num)) || a.num.localeCompare(b.num));
    const items: RefPart[][] = [];
    for (let i = 0; i < sorted.length;) {
      let j = i;
      while (j + 1 < sorted.length && /^\d+$/.test(sorted[j].num) && /^\d+$/.test(sorted[j + 1].num)
        && Number(sorted[j + 1].num) === Number(sorted[j].num) + 1) j++;
      if (j - i >= 2) items.push([{ name: sorted[i].name, text: wrap(sorted[i].num) }, '–', { name: sorted[j].name, text: wrap(sorted[j].num) }]);
      else for (let k = i; k <= j; k++) items.push([{ name: sorted[k].name, text: wrap(sorted[k].num) }]);
      i = j + 1;
    }
    const plural = sorted.length > 1;
    sections.push([`${w[(full ? 2 : 0) + (plural ? 1 : 0)]} `, ...joinList(items, [', '], [' and '])]);
  }
  return joinList(sections, [', '], [' and ']);
}

/** The plain text of a reference. */
export const refPlain = (parts: RefPart[]): string => parts.map((p) => (typeof p === 'string' ? p : p.text)).join('');

/** Whether any name of a reference is unknown. */
export const refMissing = (parts: RefPart[]): boolean => parts.some((p) => typeof p !== 'string' && !!p.missing);
