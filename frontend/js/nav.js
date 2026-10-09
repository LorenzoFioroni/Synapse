// Browser history for moving between the library and notes. Each switch of workspace and each
// note opened is a history entry, so Back/Forward (mouse buttons, ⌘[ / Alt+←) go back to the
// previous note or to the library view and selection you came from.
//
// Entries are kept in history.state.zc:
//   { ws: 'notes', path }                     a note (path null = Notes with nothing open)
//   { ws: 'library', view, sel: [ids] }       the library (kept up to date while you work there)

let restoring = false;

const same = (a, b) => !!a && !!b && a.ws === b.ws && (a.ws === 'library' || a.path === b.path);

export const navCurrent = () => history.state?.zc || null;

/** A new place: becomes a history entry (or updates the current one if it is the same place). */
export function navPush(entry) {
  if (restoring) return;
  const cur = navCurrent();
  if (same(cur, entry)) history.replaceState({ ...history.state, zc: { ...cur, ...entry } }, '');
  else history.pushState({ zc: entry }, '');
}

/** Update the current entry (e.g. the library selection changed) without adding one. */
export function navUpdate(ws, patch) {
  if (restoring) return;
  const cur = navCurrent();
  if (cur?.ws === ws) history.replaceState({ ...history.state, zc: { ...cur, ...patch } }, '');
}

export function initNav(initial, onNavigate) {
  history.replaceState({ ...history.state, zc: initial }, '');   // what is on screen now (also after a reload)
  window.addEventListener('popstate', async (e) => {
    const entry = e.state?.zc;
    if (!entry) return;
    restoring = true;
    try { await onNavigate(entry); } finally { restoring = false; }
  });
}
