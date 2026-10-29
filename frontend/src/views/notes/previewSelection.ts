// Native drag selection follows DOM coordinates. Keep its preview stable until the mouse is
// released, then reveal the selected source after CodeMirror has read the final DOM selection.
import type { EditorSelection } from '@codemirror/state';
import type { EditorView } from '@codemirror/view';
import type { CM } from './editor.js';

interface HeldSelection { selection: EditorSelection; focused: boolean }

export function previewSelection(cm: CM) {
  const hold = cm.StateEffect.define<HeldSelection | null>();
  const held = cm.StateField.define<HeldSelection | null>({
    create: () => null,
    update(value, tr) {
      if (tr.docChanged) return null;
      for (const e of tr.effects) if (e.is(hold)) return e.value;
      return value;
    },
  });
  const events = cm.ViewPlugin.fromClass(class {
    frame: number | null = null;
    readonly doc: Document;
    readonly win: Window;
    constructor(readonly view: EditorView) {
      this.doc = view.dom.ownerDocument;
      this.win = this.doc.defaultView!;
      view.dom.addEventListener('mousedown', this.start, true);
      this.doc.addEventListener('mouseup', this.end, true);
      this.doc.addEventListener('pointercancel', this.end, true);
      this.doc.addEventListener('dragend', this.end, true);
      this.win.addEventListener('blur', this.end);
      view.dom.addEventListener('keydown', this.end, true);
    }
    start = (event: MouseEvent) => {
      if (event.button !== 0) return;
      if (this.frame !== null) this.win.cancelAnimationFrame(this.frame);
      this.frame = null;
      this.view.dispatch({ effects: hold.of({ selection: this.view.state.selection, focused: this.view.hasFocus }) });
    };
    end = () => {
      if (!this.view.state.field(held, false) || this.frame !== null) return;
      this.frame = this.win.requestAnimationFrame(() => {
        this.frame = null;
        const previous = this.view.state.field(held, false);
        if (previous) this.view.dispatch({
          effects: hold.of(null),
          // Contents links can scroll elsewhere without moving the caret.
          scrollIntoView: this.view.hasFocus && !previous.selection.eq(this.view.state.selection),
        });
      });
    };
    destroy() {
      if (this.frame !== null) this.win.cancelAnimationFrame(this.frame);
      this.view.dom.removeEventListener('mousedown', this.start, true);
      this.doc.removeEventListener('mouseup', this.end, true);
      this.doc.removeEventListener('pointercancel', this.end, true);
      this.doc.removeEventListener('dragend', this.end, true);
      this.win.removeEventListener('blur', this.end);
      this.view.dom.removeEventListener('keydown', this.end, true);
    }
  });
  return { held, extension: [held, events] };
}
