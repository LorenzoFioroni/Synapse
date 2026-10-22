// What core plugins add to the notes editor while they run (frontend/src/plugins/core/). The
// editor asks these at the moment it needs them, so a plugin turned on or off takes effect in the
// open editor too. They hand CodeMirror-level things over, which is why only core plugins (built
// with the app, sharing its CodeMirror) use them; plugins of others get a wrapped editor API.
import type { GhostSource } from './ghost.js';

export const noteHooks: {
  /** Where ghost-text suggestions come from (Autocompletion), or null for none. */
  ghost: GhostSource | null;
  /** Called after a note's editor was made (to load a model before the first suggestion, say). */
  editorOpened: Set<() => void>;
} = { ghost: null, editorOpened: new Set() };

/** The ghost-text source the editor gets: whichever one a plugin set, at the time it asks. */
export const ghostSource: GhostSource = {
  ready: () => !!noteHooks.ghost?.ready(),
  complete: (prefix, suffix, signal) => (noteHooks.ghost ? noteHooks.ghost.complete(prefix, suffix, signal) : Promise.resolve('')),
  get delay() { return noteHooks.ghost?.delay; },
};
