// "Ask AI" for the open note: the button in the note's header and the palette command. The words
// selected in the note (in the editor, or in the read view) go along as the context.
import { askAi } from '../../ai/appHost.js';
import { aiConfigured } from '../../ai/status.js';
import { ws } from './workspace.js';

/** The words selected in the open note, if any. */
function selectedText(): string {
  const view = ws.editor?.view;
  if (ws.mode !== 'read' && view) {
    const { from, to } = view.state.selection.main;
    return from === to ? '' : view.state.sliceDoc(from, to);
  }
  const sel = window.getSelection();
  const read = ws.root.querySelector('.note-read');
  return sel && !sel.isCollapsed && read && sel.anchorNode && read.contains(sel.anchorNode) ? sel.toString() : '';
}

export function askAboutNote(): Promise<void> {
  const path = ws.session?.path;
  if (!path) return Promise.resolve();
  const selection = selectedText().trim();
  return askAi({ kind: 'note', path, ...(selection ? { selection } : {}) });
}

/** Show the header button only when Ask AI is set up. */
export function paintAskButton(): void {
  const b = ws.root?.querySelector<HTMLElement>('[data-act="ask-ai"]');
  if (b) b.hidden = !aiConfigured();
}
