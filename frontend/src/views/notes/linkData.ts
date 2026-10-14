// What a note links to, as the renderer needs it: [[target]] -> path and citation key -> item,
// from the backend's answer to GET /api/notes/links (the backend decides what resolves). No DOM.
import type { CiteEntry, NoteLinks } from '../../types.js';

export interface LinkData {
  notes: Map<string, string | null>;               // normTarget(target) -> path (null: no such note)
  cites: Map<string, Omit<CiteEntry, 'key'>>;       // key -> library item
}

/** How a [[target]] is looked up: case-insensitive, `\` as `/`, `.md` optional. */
export const normTarget = (t: string) => t.trim().replace(/\\/g, '/').toLowerCase().replace(/\.md$/, '');

export function linkDataOf(res: Pick<NoteLinks, 'outgoing'>): LinkData {
  const notes: LinkData['notes'] = new Map(), cites: LinkData['cites'] = new Map();
  for (const l of res.outgoing) {
    if (l.kind === 'note' || l.kind === 'embed') notes.set(normTarget(l.target), l.resolved || null);
    if (l.kind === 'cite' && l.item) cites.set(l.target, l.item);
  }
  return { notes, cites };
}
