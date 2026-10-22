// What core plugins add to the notes editor while they run (frontend/src/plugins/core/). The
// editor asks these at the moment it needs them, so a plugin turned on or off takes effect in the
// open editor too. They hand CodeMirror-level things over, which is why only core plugins (built
// with the app, sharing its CodeMirror) use them; plugins of others get a wrapped editor API.
import type { GhostSource } from './ghost.js';

/** What the graph needs from the notes view. */
export interface NotesGraphContext {
  root: HTMLElement;
  openNote(path: string): void;
  currentPath(): string | null;
  openGraph(focus: string | null): void;   // the full graph, with a history entry
  onGlobalClosed?(): void;
}

/** The graph in Notes (the Graph plugin): a local graph in the side panel, the full graph in
    place of the editor. */
export interface GraphHook {
  /** The notes view is there: start (once per hook). */
  attach(ctx: NotesGraphContext): void;
  /** The side panel's section (made once; the notes view puts it at the top of the panel). */
  localSection(): HTMLElement;
  showLocal(path: string | null): void;
  /** A note was saved, moved or deleted. */
  changed(): void;
  openGlobal(focus: string | null): Promise<void>;
  closeGlobal(opts?: { push?: boolean }): void;
  globalOpen(): boolean;
}

export const noteHooks: {
  /** Where ghost-text suggestions come from (Autocompletion), or null for none. */
  ghost: GhostSource | null;
  /** Called after a note's editor was made (to load a model before the first suggestion, say). */
  editorOpened: Set<() => void>;
  /** The graph (Graph), or null. Set it with setGraph, so the notes view hears of it. */
  graph: GraphHook | null;
} = { ghost: null, editorOpened: new Set(), graph: null };

const graphListeners = new Set<() => void>();
/** Set (or with null, take away) the graph; the notes view puts it in place or removes it. */
export function setGraph(g: GraphHook | null): void {
  noteHooks.graph = g;
  for (const fn of graphListeners) { try { fn(); } catch (e) { console.error(e); } }
}
export function onGraphChange(fn: () => void): void { graphListeners.add(fn); }

/** The ghost-text source the editor gets: whichever one a plugin set, at the time it asks. */
export const ghostSource: GhostSource = {
  ready: () => !!noteHooks.ghost?.ready(),
  complete: (prefix, suffix, signal) => (noteHooks.ghost ? noteHooks.ghost.complete(prefix, suffix, signal) : Promise.resolve('')),
  get delay() { return noteHooks.ghost?.delay; },
};
