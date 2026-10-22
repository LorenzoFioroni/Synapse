// What core plugins add to the notes editor while they run (frontend/src/plugins/core/). The
// editor asks these at the moment it needs them, so a plugin turned on or off takes effect in the
// open editor too. They hand CodeMirror-level things over, which is why only core plugins (built
// with the app, sharing its CodeMirror) use them; plugins of others get a wrapped editor API.
import type MarkdownIt from 'markdown-it';
import type { GhostSource } from './ghost.js';
import type { Scan } from './mdscan.js';

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

/** A figure of a note, found in its text (offsets into that text). */
export interface FigureInText {
  kind: 'image' | 'gallery';
  from: number;              // start of the image line, or of the gallery's opening fence
  to: number;                // end of the caption line, or of the closing fence
  captionFrom?: number;      // an image's caption line
  number: number | null;     // null: no caption, not numbered (galleries only)
  labels: string[];          // the figure's own \label names
  gallery?: { panels: { labels: string[] }[] };   // a gallery's panels, with theirs
}

/** A note's figures in order, and what each \label stands for ("2", "2(b)"). */
export interface Figures { figures: FigureInText[]; labels: Map<string, string> }

/** Galleries and numbered figures (the Figures plugin). Without it a ```gallery block reads as
    the Markdown it holds, and an image's caption line as the text it is. */
export interface FiguresHook {
  /** Add the rules and renderers to a Markdown renderer (render.ts). They run before the
      renderer's own fallback for galleries, and set env.figLabels for \ref. */
  markdown(md: MarkdownIt): void;
  /** The figures of a note's text (the live preview); ``scanned``: mdscan.scan of that text. */
  figuresOf(text: string, scanned?: Scan): Figures;
  /** How a figure's number is written: "Fig. 3". */
  label(n: number): string;
  /** A note's Markdown for other programs, figures written out (portable.ts). */
  portable(markdown: string): string;
  /** An exported copy of a rendered note: carousels become static figures (exportNote.ts). */
  exportStatic(root: HTMLElement): void;
}

interface Hooks {
  /** Where ghost-text suggestions come from (Autocompletion), or null for none. */
  ghost: GhostSource | null;
  /** Called after a note's editor was made (to load a model before the first suggestion, say). */
  editorOpened: Set<() => void>;
  /** The graph (Graph), or null. Set it with setNoteHook, so the notes view hears of it. */
  graph: GraphHook | null;
  /** Galleries and figures (Figures), or null. Set it with setNoteHook. */
  figures: FiguresHook | null;
}

export const noteHooks: Hooks = { ghost: null, editorOpened: new Set(), graph: null, figures: null };

/** Goes up whenever a hook is set or taken away (renderers rebuild when it changed). */
export let hooksVersion = 0;

type Settable = 'graph' | 'figures';
const listeners = new Set<(key: Settable) => void>();
/** Set (or with null, take away) a hook; the notes view puts it in place or removes it and draws
    the open note again. */
export function setNoteHook<K extends Settable>(key: K, value: Hooks[K]): void {
  noteHooks[key] = value;
  hooksVersion++;
  for (const fn of listeners) { try { fn(key); } catch (e) { console.error(e); } }
}
export function onNoteHookChange(fn: (key: Settable) => void): void { listeners.add(fn); }

/** The ghost-text source the editor gets: whichever one a plugin set, at the time it asks. */
export const ghostSource: GhostSource = {
  ready: () => !!noteHooks.ghost?.ready(),
  complete: (prefix, suffix, signal) => (noteHooks.ghost ? noteHooks.ghost.complete(prefix, suffix, signal) : Promise.resolve('')),
  get delay() { return noteHooks.ghost?.delay; },
};
