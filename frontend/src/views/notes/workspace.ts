// What the parts of the notes workspace share: the open note and its editor, the notes listing,
// what the open note links to, and the library items notes can cite. notesView.ts sets it up
// and fills in `hooks`, through which the other parts reach back to it.
import { state } from '../../store.js';
import type { CiteEntry, NoteDetails, NoteHistory, NoteLinks, NotesListing } from '../../types.js';
import type { MD, NoteEditor } from './editor.js';
import { baseMacros, type Macros } from './macros.js';
import type { NoteSession } from './noteSession.js';
import type { Renderer } from './render.js';

export type Mode = 'live' | 'source' | 'read';

export const ws = {
  root: null as unknown as HTMLElement,          // #notes-view (set by initNotes)
  editor: null as NoteEditor | null,             // created once, on first use
  session: null as NoteSession | null,           // the open note
  info: null as NoteDetails | null,              // its index entry (title, headings, tags…)
  overview: { notes: [], folders: [], stats: { notes: 0, links: 0 } } as NotesListing,
  mode: 'live' as Mode,
  editMode: 'live' as Exclude<Mode, 'read'>,     // what Cmd+E returns to
  lib: null as MD | null,                        // markdown-it + KaTeX, once loaded
  renderer: null as Renderer | null,             // for tables and math in the live preview
  // What the backend says the open note links to (refreshed after each save).
  linkData: { notes: new Map<string, string | null>(), cites: new Map<string, Omit<CiteEntry, 'key'>>() },
  linkResp: null as (NoteLinks & { path_for: string }) | null,   // last /notes/links answer (for the side panel)
  // Citable library items (for autocomplete and citation cards), keyed by citation key.
  library: { list: [] as CiteEntry[], byKey: new Map<string, CiteEntry>() },
  aiHist: null as NoteHistory | null,            // GET /api/notes/history of the open note
};

/** Set by notesView.ts: what the other parts call back into. */
export const hooks = {
  openNote: (_path: string): Promise<void> => Promise.resolve(),
  /** Open ``path`` now (not queued behind other opens): for code that is already inside an open. */
  doOpen: (_path: string): Promise<void> => Promise.resolve(),
  closeCurrent: (): Promise<void> => Promise.resolve(),
  setMode: (_mode: Mode, _options?: { focus?: boolean }): void => {},
  openNotesGraph: (_focus?: string | null): Promise<void> => Promise.resolve(),
};

export const notesOpen = () => document.body.dataset.workspace === 'notes';
export const notesEnabled = () => state.settings.notes_enabled !== false;

export const stemOf = (p: string) => p.split('/').pop()!.replace(/\.md$/i, '');
export const parentOf = (p: string) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');
export const nameOf = (p: string) => p.split('/').pop()!;
export const within = (p: string, folder: string) => p.startsWith(`${folder}/`);
export const normTarget = (t: string) => t.trim().replace(/\\/g, '/').toLowerCase().replace(/\.md$/, '');

/** Path a [[target]] goes to: a path, null (no such note) or undefined (not known yet). */
export function resolveNote(target: string): string | null | undefined {
  const t = normTarget(target || '');
  if (!t) return ws.session?.path;
  return ws.linkData.notes.has(t) ? ws.linkData.notes.get(t) : undefined;
}
/** Item info for a key: what the backend resolved for this note, else the library list. */
export const citeInfo = (key: string) => ws.linkData.cites.get(key) || ws.library.byKey.get(key) || null;
/** Full info for cards (with PDF and paper note), from the library list. */
export const itemInfo = (key: string): CiteEntry | null => ws.library.byKey.get(key) || (ws.linkData.cites.get(key) as CiteEntry | undefined) || null;

/** Equation numbering and the KaTeX macros every note starts from: the physics commands and your own (Settings). */
export const equationMode = () => (state.settings.notes_equation_numbers === 'all' ? 'all' : 'labelled');
export const noteMacros = (): Macros => baseMacros({ physics: state.settings.notes_math_physics !== false, text: state.settings.notes_math_macros || '' }).macros;
