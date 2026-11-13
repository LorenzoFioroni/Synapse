// What to tell the user after a note or folder was moved: the links Synapse rewrote so they keep
// working, and the ones it could not (a note edited at the same moment), by note name. No DOM.
import type { NoteMoved } from '../../types.js';
import { plural } from '../../util.js';

const stem = (path: string) => (path.split('/').pop() || path).replace(/\.md$/i, '');

/** One sentence per outcome; empty when no link was involved. */
export function moveMessage(res: Pick<NoteMoved, 'updated_links' | 'broken_links'>): string {
  const done = res.updated_links || [];
  const broken = res.broken_links || [];
  const parts: string[] = [];
  if (done.length) parts.push(`Updated ${plural(done.reduce((n, u) => n + u.count, 0), 'link')} in ${plural(done.length, 'note')}.`);
  if (broken.length) {
    const names = [...new Set(broken.map((b) => stem(b.path)))];
    parts.push(`${plural(broken.length, 'link')} in ${names.slice(0, 3).join(', ')}${names.length > 3 ? '…' : ''} could not be updated and no longer resolve${broken.length === 1 ? 's' : ''}.`);
  }
  return parts.join(' ');
}
