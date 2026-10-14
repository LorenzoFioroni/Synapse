// Claude's edits to the open note (made through the MCP server): the bar above the note that
// offers to show or undo the latest one, and putting an earlier version back. The history itself
// is read and shown by aiEdits.ts.
import { api } from '../../api.js';
import { toast, toastError } from '../../ui/toast.js';
import type { NoteHistory } from '../../types.js';
import { modLabel } from '../../util.js';
import { barHtml, editsDialog, editsOf, markSeen, pendingEdit, showEdit } from './aiEdits.js';
import { renderRead } from './readView.js';
import { paintSide } from './sidePanel.js';
import { ws } from './workspace.js';

export async function loadAiEdits(): Promise<NoteHistory | null> {
  const path = ws.session?.path;
  if (!path) return null;
  const hist = await editsOf(path);
  if (ws.session?.path !== path) return null;
  ws.aiHist = hist;
  paintAiBar();
  paintSide();
  return hist;
}

export function paintAiBar(): void {
  const { session, aiHist } = ws;
  const bar = ws.root.querySelector<HTMLElement>('.note-ai-bar')!;
  const e = session && aiHist?.path === session.path && !session.dirty ? pendingEdit(aiHist, session.saved.hash) : null;
  bar.hidden = !e;
  bar.innerHTML = e ? barHtml(e) : '';
}

/** A version saved elsewhere was just loaded into the open note. */
export async function remoteArrived(path: string): Promise<void> {
  const hist = await loadAiEdits();
  const session = ws.session;
  const e = hist && session?.path === path ? pendingEdit(hist, session.saved.hash) : null;
  if (e) toast(`Claude edited this note. ${modLabel}Z in the note undoes it.`, { timeout: 6000 });
  else toast('Updated with changes made in another window.');
}

/** Put an earlier version in the editor, as one undoable step, and save it. */
async function putBack(text: string): Promise<void> {
  const { session, editor } = ws;
  if (!session || !editor || session.status === 'conflict') return;
  editor.replace(text, { userEvent: 'restore' });
  if (ws.mode === 'read') renderRead();
  await session.save();
  paintAiBar();
  toast(`Restored the version before Claude’s change. ${modLabel}Z in the note brings the change back.`, { type: 'success' });
}

/** A button of the bar or the side panel: 'ai-edits', 'ai-show', 'ai-undo' or 'ai-dismiss'. */
export async function onAiAction(act: string, id: string | undefined): Promise<void> {
  const session = ws.session;
  if (!session) return;
  const ctx = () => ({ savedHash: session.saved.hash, currentText: session.text });
  try {
    if (act === 'ai-edits') {
      const hist = await loadAiEdits();
      if (!hist?.entries.length) return;
      const text = await editsDialog(hist, ctx());
      if (text !== null) await putBack(text);
      return;
    }
    const e = ws.aiHist?.entries.find((x) => x.id === id);
    if (!e || !id) return;
    if (act === 'ai-dismiss') { markSeen(id); paintAiBar(); return; }
    if (act === 'ai-show') {
      const text = await showEdit(e, ctx());
      if (text !== null) await putBack(text);
      return;
    }
    if (act === 'ai-undo') await putBack((await api.noteVersion(id)).before);
  } catch (err) { toastError(err); }
}
