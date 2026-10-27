// How the notes history is listed (no DOM, so it can be tested in Node): the user's own edits
// close together become one row, as Overleaf groups them; every AI change, rename, delete,
// restore and change made outside Synapse stays a row of its own.
import type { HistoryBy, HistoryKind } from '../../types.js';

/** What every listed change has (a note's version, or a change to any notes). */
export interface Listed {
  id: string;
  at: string;
  by: HistoryBy;
  kind?: HistoryKind;
  device?: string | null;
  agent?: string | null;
  summary: string;
  status?: string;
  milestones?: { id: string; label: string }[];
  /** A note's version: hashes of its text before and after. */
  before_hash?: string | null;
  after_hash?: string | null;
  /** A change to all notes: the state after it, and before it. */
  tree?: string;
  parent_tree?: string | null;
}

export interface Group<T extends Listed> {
  /** Newest first, like the history. */
  entries: T[];
}

export const GROUP_GAP_MS = 30 * 60 * 1000;   // edits less than this apart belong together
export const THIS_COMPUTER = 'This computer';

/** Your edits that end exactly where they began (changed, then undone): nothing to show. */
export function changesNothing(g: Group<Listed>): boolean {
  const newest = g.entries[0], oldest = g.entries[g.entries.length - 1];
  if (g.entries.some((e) => (e.kind ?? '') !== 'edit' || e.milestones?.length)) return false;
  if (oldest.before_hash !== undefined || newest.after_hash !== undefined) {
    return !!oldest.before_hash && oldest.before_hash === newest.after_hash;
  }
  return !!oldest.parent_tree && oldest.parent_tree === newest.tree;
}

/** Group consecutive edits by the user on the same device, each within ``gap`` of the next, and
    leave out the groups that change nothing in the end (the history drops such detours as they
    happen; these are older ones). A milestone ends a group: its version stays a row of its own. */
export function groupEdits<T extends Listed>(entries: T[], gap = GROUP_GAP_MS): Group<T>[] {
  const out: Group<T>[] = [];
  let cur: Group<T> | null = null;
  for (const e of entries) {
    const last: T | undefined = cur?.entries[cur.entries.length - 1];
    const joins = cur && last && (e.kind ?? '') === 'edit' && (last.kind ?? '') === 'edit'
      && (e.device ?? THIS_COMPUTER) === (last.device ?? THIS_COMPUTER)
      && !e.milestones?.length
      && Math.abs(new Date(last.at).getTime() - new Date(e.at).getTime()) < gap;
    if (joins) cur!.entries.push(e);
    else { cur = { entries: [e] }; out.push(cur); }
  }
  return out.filter((g) => !changesNothing(g));
}

/** Who made a change, for a row: "You", "You · iPad", the AI app, "Outside Synapse". */
export function whoMade(e: Pick<Listed, 'by' | 'device' | 'agent'>): string {
  if (e.by === 'ai') return e.agent || 'An AI app';
  if (e.by === 'outside') return 'Outside Synapse';
  return e.device && e.device !== THIS_COMPUTER ? `You · ${e.device}` : 'You';
}

/** What a version of one note did to it, in a few words (the commit subject names the note). */
export function whatHappened(e: Listed): string {
  switch (e.kind) {
    case 'edit': return e.status === 'A' ? 'Created' : 'Edited';
    case 'outside': return e.status === 'A' ? 'Created outside Synapse' : e.status === 'D' ? 'Deleted outside Synapse' : 'Changed outside Synapse';
    case 'delete': return 'Deleted';
    case 'start': return 'Start of the history';
    default: return e.summary || 'Changed';
  }
}

/** The icon of a kind of change (icons.ts names). */
export function kindIcon(kind: HistoryKind | undefined): string {
  return ({ edit: 'edit', ai: 'sparkle', outside: 'open', rename: 'move', delete: 'trash', restore: 'restore', bulk: 'key', start: 'flag', earlier: 'flag' } as Record<string, string>)[kind || ''] || 'edit';
}

/** "14:02" today, "Mon 14:02" this week, else the date and time. */
export function when(iso: string, now = new Date()): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  const days = (now.getTime() - d.getTime()) / 86400000;
  if (d.toDateString() === now.toDateString()) return time;
  if (days < 6) return `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${time}`;
  return `${d.toLocaleDateString(undefined, { year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric', month: 'short', day: 'numeric' })} ${time}`;
}

/** The span of a group, oldest to newest ("14:02–15:30"). */
export function span(g: Group<Listed>, now = new Date()): string {
  const newest = g.entries[0], oldest = g.entries[g.entries.length - 1];
  if (newest === oldest) return when(newest.at, now);
  const a = when(oldest.at, now), b = when(newest.at, now);
  if (a === b) return a;   // within the same minute
  const sameDay = new Date(oldest.at).toDateString() === new Date(newest.at).toDateString();
  return sameDay ? `${a}–${new Date(newest.at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}` : `${a} – ${b}`;
}
