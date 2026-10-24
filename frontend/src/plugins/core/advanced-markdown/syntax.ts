// Source ranges shared by Markdown rendering and live preview. Code, math and escapes win.
import { scan } from '../../../views/notes/mdscan.js';

export interface Spoiler { from: number; to: number; block: boolean }
export const escaped = (src: string, at: number): boolean => {
  let n = 0; while (at > 0 && src[--at] === '\\') n++;
  return n % 2 === 1;
};

export function inlineEnd(src: string, from: number, protectedSpans = scan(src).spans): number {
  if (!src.startsWith('||', from) || src[from + 2] === '|' || src[from - 1] === '|' || escaped(src, from)) return -1;
  const lineEnd = src.indexOf('\n', from);
  const max = lineEnd < 0 ? src.length : lineEnd;
  for (let at = from + 2; at < max - 1; at++) {
    if (src.startsWith('||', at) && src[at + 2] !== '|' && src[at - 1] !== '|'
      && !escaped(src, at) && !protectedSpans.some((sp) => at >= sp.from && at < sp.to)) {
      return src.slice(from + 2, at).trim() ? at + 2 : -1;
    }
  }
  return -1;
}

export function spoilerSpans(src: string): Spoiler[] {
  const spans = scan(src).spans;
  const protectedAt = (at: number) => spans.some((sp) => at >= sp.from && at < sp.to);
  const out: Spoiler[] = [];
  const lines = [...src.matchAll(/[^\n]*(?:\n|$)/g)].filter((m) => m[0]);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const at = line.index!;
    if (!/^ {0,3}:::spoiler(?:\s+[^\n]*)?\s*$/.test(line[0].trimEnd()) || protectedAt(at)) continue;
    let depth = 1;
    for (let j = i + 1; j < lines.length; j++) {
      if (protectedAt(lines[j].index!)) continue;
      const text = lines[j][0].trimEnd();
      if (/^ {0,3}:::spoiler(?:\s+.*)?$/.test(text)) depth++;
      if (/^ {0,3}:::\s*$/.test(text) && --depth === 0) {
        out.push({ from: at, to: lines[j].index! + lines[j][0].replace(/\n$/, '').length, block: true });
        i = j; break;
      }
    }
  }
  for (let at = 0; at < src.length - 1; at++) {
    if (protectedAt(at) || out.some((sp) => at >= sp.from && at < sp.to)) continue;
    const end = inlineEnd(src, at, spans);
    if (end > at) { out.push({ from: at, to: end, block: false }); at = end - 1; }
  }
  return out.sort((a, b) => a.from - b.from);
}
