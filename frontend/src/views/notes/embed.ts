// What an embedded note (![[note]] or ![[note#Heading]] in read mode) shows. It is rendered with
// the embedded note's own link data, so its citations and [[links]] look as they do when that
// note is open, and its own embeds carry their path (richRender.ts fills them, up to a depth).
// No DOM (tests/js/embed.test.mjs).
import type { NoteLinks } from '../../types.js';
import { linkDataOf, normTarget } from './linkData.js';
import { splitFrontmatter } from './mdscan.js';
import { type MdLib, type RendererOptions, createRenderer, slugHeading } from './render.js';

/** Text of one section (from a heading to the next heading of the same or higher level). */
export function section(body: string, heading: string | undefined): string {
  if (!heading) return body;
  const lines = body.split('\n');
  const want = slugHeading(heading);
  let start = -1, level = 0;
  for (let i = 0; i < lines.length; i++) {
    const m = /^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$/.exec(lines[i]);
    if (!m) continue;
    if (start < 0 && slugHeading(m[2]) === want) { start = i; level = m[1].length; continue; }
    if (start >= 0 && m[1].length <= level) return lines.slice(start, i).join('\n');
  }
  return start >= 0 ? lines.slice(start).join('\n') : `*Heading “${heading}” not found.*`;
}

/**
 * HTML for the note at `path` (its `text`, or the section under `heading`), embedded in another.
 * `links` is GET /api/notes/links for that note (null if it could not be loaded); `opts` is what
 * the embedding note renders with, the fallback for what `links` does not say.
 */
export function embedHtml(lib: MdLib, path: string, text: string, heading: string | undefined,
  links: Pick<NoteLinks, 'outgoing'> | null, opts: RendererOptions = {}): string {
  const data = links ? linkDataOf(links) : null;
  const t = text.replace(/\r\n?/g, '\n');
  return createRenderer(lib, {
    ...opts,
    lineOffset: undefined,   // data-line would point into the embedding note
    citeInfo: (key) => data?.cites.get(key) || opts.citeInfo?.(key) || null,
    resolveNote: (target) => {
      const n = normTarget(target || '');
      if (!n) return path;   // [[#Heading]] in the embedded note means the embedded note
      return data?.notes.has(n) ? data.notes.get(n) : opts.resolveNote?.(target);
    },
  }).render(section(t.slice(splitFrontmatter(t).bodyStart), heading));
}
