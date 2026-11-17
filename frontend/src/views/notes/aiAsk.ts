// Ask AI and the open note: what it offers the panel as context (the note, and the words selected in
// it, in the editor or in the read view) and the palette command that adds them.
import { askAi } from '../../ai/appHost.js';
import { type Attached, describe } from '../../ai/context.js';
import { ws } from './workspace.js';

/** The words selected in the open note, if any. */
function selectedText(): string {
  const view = ws.editor?.view;
  if (ws.mode !== 'read' && view) {
    const { from, to } = view.state.selection.main;
    return from === to ? '' : view.state.sliceDoc(from, to);
  }
  const sel = window.getSelection();
  const read = ws.root?.querySelector('.note-read');
  return sel && !sel.isCollapsed && read && sel.anchorNode && read.contains(sel.anchorNode) ? sel.toString() : '';
}

/** The open note and its selection, as context the panel can add. */
export function noteHere(): Attached[] {
  const path = ws.session?.path;
  if (!path) return [];
  const selection = selectedText().trim();
  const out = [describe({ kind: 'note', path })];
  if (selection) out.push(describe({ kind: 'note', path, selection }));
  return out;
}

/** The palette command: add the selection, or else the note, to the next question. */
export function askAboutNote(): Promise<void> {
  const here = noteHere();
  return here.length ? askAi(here[here.length - 1]) : askAi();
}
