// Line diff for the conflict dialog (LCS after trimming the common start and end).

const MAX_CELLS = 4_000_000;

export interface DiffRow { op: ' ' | '-' | '+'; text: string }
export type CompactRow = DiffRow | { op: '…'; count: number };

/** Returns [{op: ' ' | '-' | '+', text}] turning `a` into `b`; null when too large to diff. */
export function lineDiff(a: string, b: string): DiffRow[] | null {
  const A = a.split('\n');
  const B = b.split('\n');
  let start = 0;
  while (start < A.length && start < B.length && A[start] === B[start]) start++;
  let endA = A.length, endB = B.length;
  while (endA > start && endB > start && A[endA - 1] === B[endB - 1]) { endA--; endB--; }
  const a2 = A.slice(start, endA), b2 = B.slice(start, endB);
  const n = a2.length, m = b2.length;
  if ((n + 1) * (m + 1) > MAX_CELLS) return null;
  // LCS table, filled from the end.
  const w = m + 1;
  const L = new Uint32Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      L[i * w + j] = a2[i] === b2[j] ? L[(i + 1) * w + j + 1] + 1 : Math.max(L[(i + 1) * w + j], L[i * w + j + 1]);
    }
  }
  const out: DiffRow[] = A.slice(0, start).map((text) => ({ op: ' ', text }));
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a2[i] === b2[j]) { out.push({ op: ' ', text: a2[i] }); i++; j++; }
    else if (L[(i + 1) * w + j] >= L[i * w + j + 1]) out.push({ op: '-', text: a2[i++] });
    else out.push({ op: '+', text: b2[j++] });
  }
  while (i < n) out.push({ op: '-', text: a2[i++] });
  while (j < m) out.push({ op: '+', text: b2[j++] });
  for (const text of A.slice(endA)) out.push({ op: ' ', text });
  return out;
}

/** Keep only changed lines plus `context` lines around them; gaps become {op: '…', count}. */
export function compactDiff(rows: DiffRow[], context = 2): CompactRow[] {
  const keep = new Array(rows.length).fill(false);
  rows.forEach((r, k) => {
    if (r.op !== ' ') for (let x = Math.max(0, k - context); x <= Math.min(rows.length - 1, k + context); x++) keep[x] = true;
  });
  const out: CompactRow[] = [];
  let gap = 0;
  rows.forEach((r, k) => {
    if (keep[k]) { if (gap) out.push({ op: '…', count: gap }); gap = 0; out.push(r); } else gap++;
  });
  if (gap) out.push({ op: '…', count: gap });
  return out;
}

/** The one change {from, to, insert} turning `a` into `b` (common start and end kept), or null if equal. */
export function changedSpan(a: string, b: string): { from: number; to: number; insert: string } | null {
  if (a === b) return null;
  let start = 0;
  const max = Math.min(a.length, b.length);
  while (start < max && a.charCodeAt(start) === b.charCodeAt(start)) start++;
  let endA = a.length, endB = b.length;
  while (endA > start && endB > start && a.charCodeAt(endA - 1) === b.charCodeAt(endB - 1)) { endA--; endB--; }
  // never split a surrogate pair
  if (start > 0 && start < a.length && /[\udc00-\udfff]/.test(a[start])) start--;
  if (endA < a.length && /[\udc00-\udfff]/.test(a[endA]) && endA > start) { endA++; endB++; }
  return { from: start, to: endA, insert: b.slice(start, endB) };
}
