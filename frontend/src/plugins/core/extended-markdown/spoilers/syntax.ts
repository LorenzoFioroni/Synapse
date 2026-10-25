// Source ranges shared by Markdown rendering and live preview. Code, math and escapes win.
// Live preview asks on every keystroke, so a note is read in one pass: only positions holding
// "||" are looked at, and code/math ranges are found by binary search rather than one by one.
import type { Spoiler } from '../../../../views/notes/hooks.js';
import { scan, type Scan } from '../../../../views/notes/mdscan.js';

export type { Spoiler } from '../../../../views/notes/hooks.js';
export const escaped = (src: string, at: number): boolean => {
  let n = 0; while (at > 0 && src[--at] === '\\') n++;
  return n % 2 === 1;
};

/** Whether an offset lies in one of `spans` (code, math, comments…), in O(log n) per question. */
export function protectedBy(spans: readonly { from: number; to: number }[]): (at: number) => boolean {
  const sorted = spans.filter((sp) => sp.to > sp.from).sort((a, b) => a.from - b.from);
  const starts: number[] = [], ends: number[] = [];
  for (const sp of sorted) {
    const last = ends.length - 1;
    if (last >= 0 && sp.from <= ends[last]) ends[last] = Math.max(ends[last], sp.to);
    else { starts.push(sp.from); ends.push(sp.to); }
  }
  return (at) => {
    let lo = 0, hi = starts.length - 1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (starts[mid] <= at) lo = mid + 1; else hi = mid - 1; }
    return hi >= 0 && at < ends[hi];
  };
}

/** Where the inline spoiler opening at `from` ends (after its closing "||"), or -1. */
export function inlineEnd(src: string, from: number, isProtected = protectedBy(scan(src).spans)): number {
  if (!src.startsWith('||', from) || src[from + 2] === '|' || src[from - 1] === '|' || escaped(src, from)) return -1;
  const lineEnd = src.indexOf('\n', from);
  const max = lineEnd < 0 ? src.length : lineEnd;
  for (let at = src.indexOf('||', from + 2); at >= 0 && at < max - 1; at = src.indexOf('||', at + 1)) {
    if (src[at + 2] !== '|' && src[at - 1] !== '|' && !escaped(src, at) && !isProtected(at)) {
      return src.slice(from + 2, at).trim() ? at + 2 : -1;
    }
  }
  return -1;
}

const OPEN = /^ {0,3}:::spoiler(?:\s+.*)?$/;
const CLOSE = /^ {0,3}:::\s*$/;

/** The note's spoilers, outermost blocks and inline ones outside them, in source order.
    `scanned`: the note's mdscan result when the caller has it already (live preview does). */
export function spoilerSpans(src: string, scanned: Pick<Scan, 'spans'> = scan(src)): Spoiler[] {
  const isProtected = protectedBy(scanned.spans);
  const blocks: Spoiler[] = [];
  const lines = [...src.matchAll(/[^\n]*(?:\n|$)/g)].filter((m) => m[0]);
  for (let i = 0; i < lines.length; i++) {
    const at = lines[i].index!;
    if (!OPEN.test(lines[i][0].trimEnd()) || isProtected(at)) continue;
    let depth = 1;
    for (let j = i + 1; j < lines.length; j++) {
      if (isProtected(lines[j].index!)) continue;
      const text = lines[j][0].trimEnd();
      if (OPEN.test(text)) depth++;
      if (CLOSE.test(text) && --depth === 0) {
        blocks.push({ from: at, to: lines[j].index! + lines[j][0].replace(/\n$/, '').length, block: true });
        i = j; break;
      }
    }
  }
  const out = blocks.slice();
  let b = 0;
  for (let at = src.indexOf('||'); at >= 0; at = src.indexOf('||', at + 1)) {
    while (b < blocks.length && blocks[b].to <= at) b++;
    if (b < blocks.length && at >= blocks[b].from) { at = blocks[b].to - 1; continue; }
    if (isProtected(at)) continue;
    const end = inlineEnd(src, at, isProtected);
    if (end > at) { out.push({ from: at, to: end, block: false }); at = end - 1; }
  }
  return out.sort((a, b) => a.from - b.from);
}
