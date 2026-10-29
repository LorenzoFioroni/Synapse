// Select original note text before it falls outside the editor's nearby-text window.
import type { SuggestionContext } from '../../plugins/api.js';
import { scan, splitFrontmatter, type Heading } from './mdscan.js';

export const CONTEXT_CHARS = 1100;
const RECENT_CHARS = 2800;
const EXCERPT_CHARS = 420;
const DEFINITION = /\b(?:defin(?:e|es|ed|ition)|denot(?:e|es|ed)|notation|convention|means|let|where|called|represents|stands for|we use|we write)\b|\bis\s+\$|\\(?:newcommand|renewcommand|DeclareMathOperator)|:=|\\coloneqq/iu;
const STOP = new Set('the and for with this that from into above below note using defined denote parameter parameters have has are was were its our their then when where which will can should would could here there beginning use let'.split(' '));

function words(text: string): Set<string> {
  return new Set((text.toLowerCase().match(/[\p{L}\p{N}]{3,}/gu) ?? []).filter(w => !STOP.has(w)));
}

function ancestry(headings: Heading[], pos: number): Heading[] {
  const path: Heading[] = [];
  for (const h of headings) {
    if (h.from >= pos) break;
    while (path.length && path[path.length - 1].level >= h.level) path.pop();
    path.push(h);
  }
  return path;
}

function frontmatterTitle(header: string | null): string {
  const title = /^title:[ \t]*(.+)$/mi.exec(header ?? '')?.[1].trim() ?? '';
  return /^(".*"|'.*')$/.test(title) ? title.slice(1, -1) : title;
}

/** All snippets are from this document before the cursor; no library or other notes are read. */
export function suggestionContext(doc: string, pos: number): SuggestionContext {
  const before = doc.slice(0, pos).replace(/\r\n?/g, '\n');
  const parsed = scan(before);
  const path = ancestry(parsed.headings, before.length);
  const rawTitle = parsed.headings.find(h => h.level === 1)?.text
    || frontmatterTitle(splitFrontmatter(before).header);
  const title = rawTitle.length <= 160 ? rawTitle : '';
  const headings: string[] = [];
  let used = title.length;
  for (const h of path) {
    if (h.level === 1 && h.text === title) continue;
    const text = `${'#'.repeat(h.level)} ${h.text}`;
    if (text.length <= 180 && used + text.length <= 460) { headings.push(text); used += text.length; }
  }
  const recentStart = Math.max(parsed.bodyStart, before.length - RECENT_CHARS);
  const line = before.slice(before.lastIndexOf('\n') + 1);
  const query = words(line + ' ' + headings.join(' '));
  const blocked = parsed.spans.filter(s => ['fence', 'comment', 'gallery', 'taskblock'].includes(s.kind));
  const candidates: { text: string; from: number; score: number; key: string }[] = [];
  const body = before.slice(parsed.bodyStart, recentStart);
  for (const m of body.matchAll(/\S[\s\S]*?(?=\n[ \t]*\n|$)/g)) {
    const start = parsed.bodyStart + m.index!;
    const end = start + m[0].length;
    // A window cut through a paragraph must not supply a half equation or a partial definition.
    if (end === recentStart && before.slice(end, end + 2) !== '\n\n') continue;
    if (blocked.some(s => s.from < end && s.to > start)) continue;
    if (/^(?:#{1,6}\s|!\[|\[[^\]]+\]:)/.test(m[0])) continue;
    const chunks: { text: string; from: number }[] = [];
    if (m[0].length <= EXCERPT_CHARS) chunks.push({ text: m[0], from: start });
    else {
      const masked = parsed.masked.slice(start - parsed.bodyStart, end - parsed.bodyStart);
      let a = 0;
      // Math/code are masked by the shared Markdown scanner, so punctuation inside them is safe.
      for (const cut of masked.matchAll(/[.!?](?=\s+[A-Z]|\s*$)/g)) {
        const b = cut.index! + 1;
        chunks.push({ text: before.slice(start + a, start + b).trim(), from: start + a });
        a = b;
      }
    }
    for (const chunk of chunks) {
      const text = chunk.text.trim();
      if (!text || text.length > EXCERPT_CHARS || !DEFINITION.test(text)) continue;
      const hits = [...words(text)].filter(w => query.has(w)).sort();
      const scope = ancestry(parsed.headings, chunk.from).at(-1);
      const active = scope && path.some(h => h.from === scope.from);
      const score = hits.length * 4 + (active ? 2 : 0) + chunk.from / Math.max(1, before.length);
      candidates.push({ text, from: chunk.from, score, key: hits.join(' ') || text });
    }
  }
  candidates.sort((a, b) => b.score - a.score);
  const picked: typeof candidates = [];
  const seen = new Set<string>();
  for (const c of candidates) {
    if (seen.has(c.key) || used + c.text.length > CONTEXT_CHARS) continue;
    picked.push(c); seen.add(c.key); used += c.text.length;
    if (picked.length === 3) break;
  }
  return { title, headings, excerpts: picked.sort((a, b) => a.from - b.from).map(c => c.text) };
}
