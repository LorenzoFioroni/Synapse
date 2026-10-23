// Autocompletion as ghost text: after a short pause in typing, a suggestion for the cursor is
// asked for (``source``: a local model, see assist.ts) and shown in grey after the cursor.
//   Tab accepts it · Esc dismisses it · Cmd/Ctrl+→ accepts its next word · Alt+\ asks now
//   typing the same characters keeps the rest · clicking it accepts it (iPad: no Tab key)
// Nothing is asked for while the citation/link list is open, in front matter or code, in the
// middle of a line, or when ``source`` says suggestions are not available.
import type { EditorState, Transaction } from '@codemirror/state';
import type { EditorView, ViewUpdate } from '@codemirror/view';
import type { SyntaxNode } from '@lezer/common';
import type { CM } from './editor.js';
import { type Ghost, canSuggestAt, consumeTyped, contextAround, nextWord } from './ghostLogic.js';

export interface GhostSource {
  /** Suggestions may be asked for now (setting on, Ollama and the model there). */
  ready(): boolean;
  /** The suggestion for the cursor between ``prefix`` and ``suffix`` ('' for none). */
  complete(prefix: string, suffix: string, signal: AbortSignal): Promise<string>;
  /** Pause in typing before asking (ms). */
  delay?: number;
  /** The provider changed or stopped: clear its pending and visible suggestion. */
  onChange?(fn: () => void): () => void;
}

const NO_GHOST = /^(InlineCode|CodeText|FencedCode|CodeBlock|CodeInfo|CodeMark|Frontmatter|URL|HTMLTag|Comment)$/;
const DELAY = 350;

/** The ghost-text extension, and the keys that go with it (to be placed before the editor's
 *  other keymaps, so Tab accepts a suggestion rather than indenting). */
export function ghostText(cm: CM, source: GhostSource) {
  const { StateField, StateEffect, EditorView, Decoration, WidgetType, ViewPlugin, keymap, syntaxTree, Transaction: Tr } = cm;
  const setGhost = StateEffect.define<Ghost | null>();

  /** Accept ``text`` (all of the suggestion, or its next word). */
  function accept(view: EditorView, part?: string): boolean {
    const g = view.state.field(field, false);
    if (!g || !source.ready()) return false;
    const insert = part ?? g.text;
    view.dispatch({
      changes: { from: g.pos, insert },
      selection: { anchor: g.pos + insert.length },
      // a whole suggestion is its own undo step; a word is like typing it (the rest stays)
      annotations: Tr.userEvent.of(part === undefined ? 'input.complete' : 'input.type'),
      scrollIntoView: true,
    });
    return true;
  }

  class GhostWidget extends WidgetType {
    constructor(readonly text: string) { super(); }
    eq(other: GhostWidget) { return other.text === this.text; }
    toDOM(view: EditorView) {
      const span = document.createElement('span');
      span.className = 'cm-ghost';
      span.textContent = this.text;
      span.title = 'Suggestion: Tab to accept, Esc to dismiss';
      span.setAttribute('aria-hidden', 'true');
      span.addEventListener('mousedown', (e) => { e.preventDefault(); accept(view); view.focus(); });
      return span;
    }
    ignoreEvent() { return true; }
  }

  const field = StateField.define<Ghost | null>({
    create: () => null,
    update(g, tr: Transaction) {
      for (const e of tr.effects) if (e.is(setGhost)) return e.value;
      if (!g) return null;
      const sel = tr.newSelection.main;
      if (tr.docChanged) {
        // typing the suggestion's own characters keeps the rest of it
        let changes = 0;
        const left: { g: Ghost | null } = { g: null };
        tr.changes.iterChanges((fromA, toA, _fromB, _toB, inserted) => { changes++; left.g = consumeTyped(g, fromA, toA, inserted.toString()); });
        if (changes !== 1 || !left.g || !tr.isUserEvent('input.type') || !sel.empty || sel.head !== left.g.pos) return null;
        return left.g;
      }
      if (tr.selection && (!sel.empty || sel.head !== g.pos)) return null;
      return g;
    },
    provide: (f) => EditorView.decorations.from(f, (g) => (g
      ? Decoration.set([Decoration.widget({ widget: new GhostWidget(g.text), side: 1 }).range(g.pos)])
      : Decoration.none)),
  });

  const inCode = (state: EditorState, pos: number) => {
    for (let n: SyntaxNode | null = syntaxTree(state).resolveInner(pos, -1); n; n = n.parent) if (NO_GHOST.test(n.name)) return true;
    return false;
  };
  const listOpen = (view: EditorView) => !!view.dom.querySelector('.cm-tooltip-autocomplete');

  const plugin = ViewPlugin.fromClass(class {
    timer: ReturnType<typeof setTimeout> | undefined;
    ctrl: AbortController | null = null;
    unsubscribe?: () => void;
    constructor(readonly view: EditorView) {
      this.unsubscribe = source.onChange?.(() => {
        this.cancel();
        view.dispatch({ effects: setGhost.of(null) });
      });
    }

    update(u: ViewUpdate) {
      if (!u.docChanged && !u.selectionSet && !u.focusChanged) return;
      this.cancel();
      if (u.focusChanged && !u.view.hasFocus) { this.clearSoon(); return; }
      const typed = u.transactions.some((tr) => tr.isUserEvent('input.type') || tr.isUserEvent('input.complete'));
      if (typed && !u.state.field(field, false)) this.schedule(false);
    }

    cancel() {
      clearTimeout(this.timer);
      this.ctrl?.abort();
      this.ctrl = null;
    }

    clearSoon() {
      // not while CodeMirror is applying an update
      queueMicrotask(() => { if (this.view.state.field(field, false)) this.view.dispatch({ effects: setGhost.of(null) }); });
    }

    schedule(explicit: boolean) {
      this.cancel();
      this.timer = setTimeout(() => this.ask(explicit), explicit ? 0 : source.delay ?? DELAY);
    }

    async ask(explicit: boolean) {
      const view = this.view;
      const state = view.state;
      const sel = state.selection.main;
      if (!source.ready() || !view.hasFocus || !sel.empty || listOpen(view) || inCode(state, sel.head)) return;
      const line = state.doc.lineAt(sel.head);
      const doc = state.doc.toString();
      const { prefix, suffix } = contextAround(doc, sel.head);
      if (!canSuggestAt(line.text.slice(sel.head - line.from), prefix, explicit)) return;
      const ctrl = new AbortController();
      this.ctrl = ctrl;
      let text = '';
      try {
        text = await source.complete(prefix, suffix, ctrl.signal);
      } catch { return; /* cancelled, or no server: no suggestion */ }
      if (ctrl.signal.aborted || this.ctrl !== ctrl) return;
      this.ctrl = null;
      // only if nothing moved meanwhile
      const now = view.state;
      if (!text || now.doc !== state.doc || now.selection.main.head !== sel.head || !view.hasFocus || listOpen(view)) return;
      view.dispatch({ effects: setGhost.of({ pos: sel.head, text }) });
    }

    destroy() { this.unsubscribe?.(); this.cancel(); }
  });

  const keys = keymap.of([
    { key: 'Tab', run: (view) => accept(view) },
    { key: 'Mod-ArrowRight', run: (view) => { const g = view.state.field(field, false); return !!g && accept(view, nextWord(g.text)); } },
    { key: 'Escape', run: (view) => {
      if (!view.state.field(field, false)) return false;
      view.dispatch({ effects: setGhost.of(null) });
      return true;
    } },
    { key: 'Alt-\\', preventDefault: true, run: (view) => {
      if (!source.ready()) return false;
      view.plugin(plugin)?.schedule(true);
      return true;
    } },
  ]);

  return { extension: [field, plugin, keys], field };
}
