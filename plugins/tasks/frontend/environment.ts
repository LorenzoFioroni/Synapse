// Plugin-owned caches. The public SDK returns the current editor and plain note snapshot.
import { currentEditor, currentNote, libraryView, librarySelection, notesEnabled } from '../../../frontend/src/plugins/task-runtime.js';
import type { NotesListing, CiteEntry, TaskNoteHooks } from '../../../frontend/src/plugins/task-runtime.js';
export { citeInfo, equationMode, noteMacros, normTarget } from '../../../frontend/src/plugins/task-runtime.js';
export const taskHooks: TaskNoteHooks = { preview: null, key: null, add: null, paintSide: null, click: null, saved: null,
  choices: null, create: null, titleHtml: null, portable: null };
export const ws = {
  get editor() { return currentEditor(); },
  get session() { return currentNote(); },
  overview: { notes: [] } as unknown as NotesListing,
  library: { list: [] as CiteEntry[], byKey: new Map<string, CiteEntry>() },
};
export const state = {
  settings: { get notes_enabled() { return notesEnabled(); } },
  get view() { return libraryView(); },
  get selection() { return librarySelection(); },
};

import type { PluginContext } from '../../../frontend/src/plugins/api.js';
import { noteIndex, noteIndexSnapshot, citationIndex, emit, on, off } from '../../../frontend/src/plugins/task-runtime.js';
export function startEnvironment(p: PluginContext): void {
  let active = true;
  p.onUnload(() => { active = false; });
  let notesGeneration = 0, libraryGeneration = 0;
  const refresh = async () => {
    const notesRequest = ++notesGeneration, libraryRequest = ++libraryGeneration;
    try {
      const [notes, cites] = await Promise.all([noteIndex(), citationIndex()]);
      if (!active) return;
      if (notesRequest === notesGeneration) ws.overview = notes;
      if (libraryRequest === libraryGeneration) ws.library = { list: cites.items, byKey: new Map(cites.items.map(item => [item.key, item])) };
      emit('tasks');
    } catch { /* Keep the last readable indexes while the server is unavailable. */ }
  };
  // This window's own note writes are omitted from its change feed; use the host's fresh snapshot.
  const notesUpdated = () => {
    if (!active) return;
    notesGeneration++;
    ws.overview = noteIndexSnapshot();
    emit('tasks');
  };
  on('notes-count', notesUpdated);
  p.onUnload(() => off('notes-count', notesUpdated));
  p.onChange(kinds => { if (kinds.includes('notes') || kinds.includes('library')) void refresh(); });
  void refresh();
}
