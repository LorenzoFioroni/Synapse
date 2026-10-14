// Browser history for moving between the library and notes. Each switch of workspace and each
// note opened is a history entry, so Back/Forward (mouse buttons, ⌘[ / Alt+←) go back to the
// previous note or to the library view and selection you came from.
//
// Entries are kept in history.state.zc:
//   { ws: 'notes', path }                     a note (path null = Notes with nothing open)
//   { ws: 'notes', path, graph: true, focus }  the graph in Notes (focus: the node it is centred on)
//   { ws: 'library', view, sel: [ids] }       the library (kept up to date while you work there)

/** A place in the app, kept in history.state.zc (see above). */
export interface NavEntry {
  ws: 'notes' | 'library';
  path?: string | null;
  graph?: boolean;
  focus?: string | null;
  view?: string;
  sel?: string[];
}

let restoring = 0;        // >0 while going Back/Forward (those must not add entries)
let snapshot: ((cur: NavEntry) => Partial<Omit<NavEntry, 'ws'>> | null | undefined) | null = null;   // fresh data for the current entry, taken before leaving it

const same = (a: NavEntry | null, b: NavEntry | null) => !!a && !!b && a.ws === b.ws
  && (a.ws === 'library' || (a.path === b.path && !!a.graph === !!b.graph && (a.focus || null) === (b.focus || null)));

export const navCurrent = (): NavEntry | null => history.state?.zc || null;

/** A new place: becomes a history entry (or updates the current one if it is the same place). */
export function navPush(entry: NavEntry): void {
  if (restoring) return;
  let cur = navCurrent();
  const fresh = cur && snapshot?.(cur);
  if (cur && fresh) { cur = { ...cur, ...fresh }; history.replaceState({ ...history.state, zc: cur }, ''); }
  if (same(cur, entry)) history.replaceState({ ...history.state, zc: { ...cur, ...entry } }, '');
  else history.pushState({ zc: entry }, '');
}

/** Update the current entry (e.g. the library selection changed) without adding one. */
export function navUpdate(ws: NavEntry['ws'], patch: Partial<Omit<NavEntry, 'ws'>>): void {
  if (restoring) return;
  const cur = navCurrent();
  if (cur?.ws === ws) history.replaceState({ ...history.state, zc: { ...cur, ...patch } }, '');
}

/** ``fn(entry)`` returns the up-to-date fields of the entry being left (e.g. the library selection). */
export function navSnapshot(fn: (cur: NavEntry) => Partial<Omit<NavEntry, 'ws'>> | null | undefined): void { snapshot = fn; }

export function initNav(initial: NavEntry, onNavigate: (entry: NavEntry) => unknown): void {
  history.replaceState({ ...history.state, zc: initial }, '');   // what is on screen now (also after a reload)
  window.addEventListener('popstate', async (e) => {
    const entry: NavEntry | undefined = e.state?.zc;
    if (!entry) return;
    restoring++;
    try { await onNavigate(entry); } finally { restoring--; }
  });
}
