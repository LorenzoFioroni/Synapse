// Numbered section labels in Markdown headings. A heading may end with one or more
// \label{sec:name}; references use the same cross-reference engine as equations and figures.
import type { Heading, Scan } from './mdscan.js';
import { scan } from './mdscan.js';

export interface SectionLabel { name: string; from: number; to: number }
export interface Section { level: number; number: string; from: number; text: string; labels: SectionLabel[]; inToc: boolean }

/** A leading H1 is the note title, not section 1. Number the sections below it from 1. */
function numberedHeadings(headings: Heading[]): Heading[] {
  return headings.length > 1 && headings[0].level === 1 && headings.slice(1).some((h) => h.level > 1)
    ? headings.slice(1) : headings;
}

/** Remove labels outside inline code and math, retaining their exact offsets. */
export function takeSectionLabels(text: string, base = 0): { text: string; labels: SectionLabel[] } {
  const labels: SectionLabel[] = [];
  const masked = text.split('');
  let tick = 0, math = false;
  for (let i = 0; i < text.length;) {
    if (!tick && text[i] === '\\' && text[i + 1] === '\\') { i += 2; continue; }
    if (text[i] === '`') {
      let j = i; while (text[j] === '`') j++;
      const run = j - i;
      if (!tick) tick = run; else if (tick === run) tick = 0;
      i = j; continue;
    }
    if (!tick && text[i] === '$') { math = !math; i++; continue; }
    if (!tick && !math && text.startsWith('\\label', i)) {
      const m = /^\\label\s*\{([^{}\n]*)\}/.exec(text.slice(i));
      if (m) {
        const name = m[1].trim();
        if (name) labels.push({ name, from: base + i, to: base + i + m[0].length });
        for (let k = i; k < i + m[0].length; k++) masked[k] = '';
        i += m[0].length; continue;
      }
    }
    i++;
  }
  return { text: masked.join('').replace(/[ \t]+$/u, '').trimEnd(), labels };
}

function numberHeadings(headings: Heading[]): Map<number, string> {
  const out = new Map<number, string>();
  const counters = [0, 0, 0, 0, 0, 0];
  const numbered = numberedHeadings(headings);
  const base = numbered.reduce((n, h) => Math.min(n, h.level), 6);
  for (const h of numbered) {
    counters[h.level - 1]++;
    counters.fill(0, h.level);
    out.set(h.from, counters.slice(base - 1, h.level).map((n) => n || 1).join('.'));
  }
  return out;
}

/** Sections and label lookup for the live preview. */
export function sectionsOf(text: string, scanned: Scan = scan(text)): { sections: Section[]; labels: Map<string, string> } {
  const nums = numberHeadings(scanned.headings);
  const lines = text.split('\n');
  const sections: Section[] = [];
  const labels = new Map<string, string>();
  for (const h of scanned.headings) {
    const raw = lines[h.line - 1] || '';
    const mark = /^ {0,3}#{1,6}\*?[ \t]+/.exec(raw);
    const contentAt = h.from + (mark?.[0].length || 0);
    const p = takeSectionLabels(raw.slice(mark?.[0].length || 0).replace(/[ \t]+#+[ \t]*$/u, ''), contentAt);
    const number = nums.get(h.from) || '';
    for (const label of p.labels) if (number && !labels.has(label.name)) labels.set(label.name, number);
    sections.push({ level: h.level, number, from: h.from, text: p.text, labels: p.labels,
      inToc: !/^ {0,3}#{1,6}\*[ \t]+/.test(raw) });
  }
  return { sections, labels };
}
