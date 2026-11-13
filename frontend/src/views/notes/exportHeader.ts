// The byline every note export carries under its title: the author set in Settings (when there is
// one) and the day of the export, not the note's creation date.
export interface ExportHeader { author: string; date: string }

/** The day of the export, as "13 November 2026" (fixed wording: the same in every format). */
export function exportDate(now: Date = new Date()): string {
  return now.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
}

export function exportHeader(author: string | undefined, now: Date = new Date()): ExportHeader {
  return { author: (author || '').replace(/\s+/g, ' ').trim(), date: exportDate(now) };
}

/** "Author · date", or the date alone. */
export function bylineText(h: ExportHeader): string {
  return [h.author, h.date].filter(Boolean).join(' · ');
}
