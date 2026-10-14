// CodeMirror 6 set-up for notes: live preview or source mode. The libraries are loaded on
// first use. Colours come from the app's CSS variables, so light and dark themes just work.
import type { HighlightStyle } from '@codemirror/language';
import type { EditorView } from '@codemirror/view';
import type { MarkdownConfig } from '@lezer/markdown';
import type { CiteEntry, NoteInfo } from '../../types.js';
import { completions } from './completion.js';
import { changedSpan } from './diff.js';
import { type PreviewDeps, livePreview } from './livePreview.js';
import { refitOnResize } from './mathFit.js';
import { splitFrontmatter } from './mdscan.js';

export type CM = typeof import('../../../vendor/cm6/cm6.js');
export type MD = typeof import('../../../vendor/md/md.js');

/** What the editor reports to the notes view. */
export interface EditorHandlers {
  onChange?(text: string): void;
  onSave?(): void;
  onBlur?(): void;
  /** A pasted or dropped image: store it and return the Markdown that shows it (null: nothing to insert). */
  onImage?(file: File): Promise<string | null> | string | null;
  onToggleRead?(): void;
}

export interface NoteEditor {
  view: EditorView;
  load(text: string): void;
  replace(text: string, options?: { userEvent?: string }): void;
  setMode(mode: 'live' | 'source'): void;
  refresh(): void;
  toggleTask(line0: number): boolean;
  insertBlock(md: string): void;
  text(): string;
  focus(): void;
  gotoLine(n: number): void;
  destroy(): void;
}

let CM: CM | null = null;
let MD: MD | null = null;
export async function loadCM(): Promise<CM> {
  CM ||= await import('../../../vendor/cm6/cm6.js');
  return CM;
}
export async function loadMD(): Promise<MD> {
  MD ||= await import('../../../vendor/md/md.js');
  return MD;
}

/**
 * Front-matter block for the Markdown parser: a `---` line at the very start of the note up to
 * the next `---` (or `...`) line. Same rule as the backend (frontmatter.split): if it is never
 * closed, it is not front-matter.
 */
function frontmatter(cm: CM): MarkdownConfig {
  return {
    defineNodes: [{ name: 'Frontmatter', block: true }],
    parseBlock: [{
      name: 'Frontmatter',
      before: 'HorizontalRule',
      parse(cx, line) {
        if (cx.lineStart !== 0 || line.text !== '---') return false;
        // the whole document: BlockContext.input exists but is not in @lezer/markdown's public types
        const input = (cx as unknown as { input: { read(from: number, to: number): string; length: number } }).input;
        const doc = input.read(0, input.length);
        const m = /\n(---|\.\.\.)(\n|$)/.exec(doc.slice(3));
        if (!m) return false;
        const end = 3 + m.index + 1 + m[1].length;
        while (cx.lineStart + line.text.length < end && cx.nextLine()) { /* consume */ }
        cx.addElement(cx.elt('Frontmatter', 0, end));
        cx.nextLine();
        return true;
      },
    }],
    props: [cm.styleTags({ Frontmatter: cm.tags.meta })],
  };
}

let HL: HighlightStyle | null = null;
/** The highlight style, shared with read mode so code looks the same in both. */
export function highlightStyle(cm: CM): HighlightStyle {
  if (HL) return HL;
  const t = cm.tags;
  HL = cm.HighlightStyle.define([
    { tag: t.heading1, class: 'cmt-h cmt-h1' },
    { tag: t.heading2, class: 'cmt-h cmt-h2' },
    { tag: t.heading3, class: 'cmt-h cmt-h3' },
    { tag: [t.heading4, t.heading5, t.heading6], class: 'cmt-h cmt-h4' },
    { tag: t.strong, class: 'cmt-strong' },
    { tag: t.emphasis, class: 'cmt-em' },
    { tag: t.strikethrough, class: 'cmt-strike' },
    { tag: [t.link, t.url], class: 'cmt-link' },
    { tag: t.monospace, class: 'cmt-code' },
    { tag: t.quote, class: 'cmt-quote' },
    { tag: t.meta, class: 'cmt-meta' },
    { tag: [t.processingInstruction, t.contentSeparator], class: 'cmt-mark' },
    { tag: t.list, class: 'cmt-list' },
    // inside fenced code blocks
    { tag: [t.keyword, t.operatorKeyword, t.modifier], class: 'cmt-kw' },
    { tag: [t.string, t.special(t.string), t.regexp], class: 'cmt-str' },
    { tag: [t.number, t.bool, t.null, t.atom], class: 'cmt-num' },
    { tag: [t.comment, t.lineComment, t.blockComment], class: 'cmt-comment' },
    { tag: [t.function(t.variableName), t.function(t.propertyName)], class: 'cmt-fn' },
    { tag: [t.typeName, t.className, t.namespace], class: 'cmt-type' },
    { tag: [t.propertyName, t.attributeName], class: 'cmt-prop' },
  ]);
  return HL;
}

/**
 * The notes editor in ``parent``. ``preview`` is what livePreview.ts needs (renderer, resolveNote,
 * citeInfo, openNote …); ``data`` gives autocomplete the cite index and the notes overview.
 */
export async function createEditor(
  parent: HTMLElement,
  handlers: EditorHandlers,
  preview: PreviewDeps,
  mode: 'live' | 'source' = 'live',
  data: { items(): CiteEntry[]; notes(): NoteInfo[] } = { items: () => [], notes: () => [] },
): Promise<NoteEditor> {
  const cm = await loadCM();
  const lp = livePreview(cm, preview);
  const modeSlot = new cm.Compartment();
  const modeExt = (m: string) => (m === 'live' ? [lp.extension, cm.EditorView.editorAttributes.of({ class: 'cm-live' })] : []);
  let currentMode = mode;
  const keys = [
    { key: 'Mod-s', preventDefault: true, run: () => { handlers.onSave?.(); return true; } },
    { key: 'Mod-e', preventDefault: true, run: () => { handlers.onToggleRead?.(); return true; } },
  ];

  const insertImages = async (view: EditorView, files: File[], pos?: number) => {
    for (const f of files) {
      const md = await handlers.onImage?.(f);
      if (!md) continue;
      const at = pos ?? view.state.selection.main.head;
      const before = at > 0 && view.state.sliceDoc(at - 1, at) !== '\n' ? '\n' : '';
      const text = `${before}${md}\n`;
      view.dispatch({ changes: { from: at, insert: text }, selection: { anchor: at + text.length } });
      pos = at + text.length;
    }
  };
  const imagesOf = (dt: DataTransfer | null) => [...(dt?.files || [])].filter((f) => f.type.startsWith('image/'));

  const extensions = () => [
    cm.history(),
    cm.drawSelection(),
    cm.dropCursor(),
    cm.highlightSpecialChars(),
    cm.EditorView.lineWrapping,
    cm.indentOnInput(),
    cm.bracketMatching(),
    cm.closeBrackets(),
    cm.highlightSelectionMatches(),
    cm.search({ top: true }),
    cm.markdown({ base: cm.markdownLanguage, codeLanguages: cm.languages, extensions: [frontmatter(cm)] }),
    cm.syntaxHighlighting(highlightStyle(cm)),
    completions(cm, data),
    modeSlot.of(modeExt(currentMode)),
    cm.EditorView.contentAttributes.of({ spellcheck: 'true', autocorrect: 'on', autocapitalize: 'sentences' }),
    cm.keymap.of([...keys, ...cm.closeBracketsKeymap, ...cm.markdownKeymap, ...cm.defaultKeymap,
      ...cm.searchKeymap, ...cm.historyKeymap, cm.indentWithTab]),
    cm.EditorView.updateListener.of((u) => {
      if (u.docChanged) handlers.onChange?.(u.state.doc.toString());
      if (u.focusChanged && !u.view.hasFocus) handlers.onBlur?.();
    }),
    cm.EditorView.domEventHandlers({
      paste(e, view) {
        const files = imagesOf(e.clipboardData);
        if (!files.length) return false;
        e.preventDefault();
        insertImages(view, files);
        return true;
      },
      drop(e, view) {
        const files = imagesOf(e.dataTransfer);
        if (!files.length) return false;
        e.preventDefault();
        const pos = view.posAtCoords({ x: e.clientX, y: e.clientY });
        insertImages(view, files, pos ?? undefined);
        return true;
      },
    }),
  ];

  const view = new cm.EditorView({ parent, state: cm.EditorState.create({ doc: '', extensions: extensions() }) });
  // wide equations shrink a little to fit when the pane changes width (mathFit.js)
  refitOnResize(view.scrollDOM, () => view.requestMeasure());

  return {
    view,
    /** Load a different note: fresh state, so undo history never crosses notes. */
    load(text) {
      // start below the front matter, so it shows as properties rather than as source
      const start = Math.min(splitFrontmatter(text).bodyStart, text.length);
      view.setState(cm.EditorState.create({ doc: text, extensions: extensions(), selection: { anchor: start } }));
    },
    /**
     * Replace the content of the current note (a version saved elsewhere, e.g. by Claude or another
     * tab). Only the part that differs is replaced, so the cursor and scroll position stay, and it
     * is its own step in the undo history: Cmd+Z takes the note back to what it was.
     */
    replace(text, { userEvent = 'remote' } = {}) {
      const cur = view.state.doc.toString();
      const ch = changedSpan(cur, text);
      if (!ch) return;
      view.dispatch({ changes: ch, annotations: cm.Transaction.userEvent.of(userEvent) });
    },
    setMode(m) {
      currentMode = m;
      view.dispatch({ effects: modeSlot.reconfigure(modeExt(m)) });
    },
    /** Redraw the preview (after link or library data changed). */
    refresh() { if (currentMode === 'live') view.dispatch({ effects: lp.refresh.of(null) }); },
    /** Tick/untick the task on a 0-based line (used by read mode). */
    toggleTask(line0) {
      const line = view.state.doc.line(line0 + 1);
      const m = /^(\s*(?:[-*+]|\d+[.)])\s+\[)([ xX])\]/.exec(line.text);
      if (!m) return false;
      const at = line.from + m[1].length;
      view.dispatch({ changes: { from: at, to: at + 1, insert: m[2] === ' ' ? 'x' : ' ' } });
      return true;
    },
    /** Insert a Markdown block as its own paragraph after the cursor's line; cursor ends after it. */
    insertBlock(md) {
      const st = view.state;
      const line = st.doc.lineAt(st.selection.main.head);
      const before = line.text.trim() ? '\n\n' : (line.number > 1 && st.doc.line(line.number - 1).text.trim() ? '\n' : '');
      const after = line.to < st.doc.length ? '\n' : '\n';
      const insert = `${before}${md.replace(/\n+$/, '')}${after}`;
      view.dispatch({ changes: { from: line.to, insert }, selection: { anchor: line.to + insert.length },
        effects: cm.EditorView.scrollIntoView(line.to + insert.length, { y: 'center' }) });
    },
    text: () => view.state.doc.toString(),
    focus: () => view.focus(),
    gotoLine(n) {
      const line = view.state.doc.line(Math.max(1, Math.min(n, view.state.doc.lines)));
      view.dispatch({ selection: { anchor: line.from }, effects: cm.EditorView.scrollIntoView(line.from, { y: 'start', yMargin: 40 }) });
      view.focus();
    },
    destroy: () => view.destroy(),
  };
}
