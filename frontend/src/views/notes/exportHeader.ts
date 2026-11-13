// The byline every note export carries under its title: the note's author and the day of the
// export, not the note's creation date.
import { splitFrontmatter } from './mdscan.js';

export interface ExportHeader { author: string; date: string }

function field(header: string | null, key: string): string {
  const m = header?.match(new RegExp(`^${key}:[ \\t]*(.*)$`, 'm'));
  return m ? m[1].trim().replace(/^(["'])(.*)\1$/, '$2').trim() : '';
}

/** The author of a note, from its front matter: a name is used as written, ``ai`` gives the app
 *  that wrote it (``agent``), and ``human`` (the default) or nothing gives ``fallback``, the name
 *  set in Settings. */
export function noteAuthor(text: string, fallback = ''): string {
  const header = splitFrontmatter(text).header;
  const author = field(header, 'author');
  if (author.toLowerCase() === 'ai') return field(header, 'agent');
  return author && author.toLowerCase() !== 'human' ? author : fallback;
}

/** The day of the export, as "13 November 2026" (fixed wording: the same in every format). */
export function exportDate(now: Date = new Date()): string {
  return now.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
}

export function exportHeader(text: string, fallbackAuthor: string | undefined, now: Date = new Date()): ExportHeader {
  return { author: noteAuthor(text, fallbackAuthor).replace(/\s+/g, ' ').trim(), date: exportDate(now) };
}

/** "Author · date", or the date alone. */
export function bylineText(h: ExportHeader): string {
  return [h.author, h.date].filter(Boolean).join(' · ');
}
