// The dialog for a note that was saved or deleted elsewhere while it had unsaved changes here
// (NoteSession detects it; nothing is overwritten until you choose).
import { openDialog } from '../../ui/dialog.js';
import { diffHtml } from './aiEdits.js';
import type { Conflict } from './noteSession.js';
import { refreshTree } from './tree.js';
import { hooks, ws } from './workspace.js';

export function conflictDialog(c: Conflict): Promise<void> {
  if (c.deleted) {
    return openDialog({
      title: 'This note was deleted elsewhere',
      subtitle: 'It was moved to the trash or deleted in another window while you were editing it.',
      dismissable: false,
      render: (b) => { b.innerHTML = '<p style="margin:0">Your text is still here. Save it again under the same name, or close it.</p>'; },
      buttons: [{ label: 'Close the note', value: 'close' }, { label: 'Save my text again', kind: 'primary', value: 'mine' }],
    }).then(async (v) => {
      if (v === 'mine') { await ws.session?.resolve('mine'); refreshTree(); } else { await hooks.closeCurrent(); refreshTree(); }
    });
  }
  const diff = diffHtml(c.theirs ?? '', c.mine);
  return openDialog({
    title: 'This note was changed elsewhere',
    subtitle: 'Another window (or another app) saved this note while you were editing it. Nothing has been overwritten.',
    wide: true,
    dismissable: false,
    render: (b) => {
      b.innerHTML = `<div class="diff-legend"><span class="d-del">− only in the saved file</span><span class="d-add">+ only in your version</span></div>${diff}`;
    },
    buttons: [{ label: 'Use the saved file', value: 'theirs' }, { label: 'Keep my version', kind: 'primary', value: 'mine' }],
  }).then((v) => ws.session?.resolve(v === 'theirs' ? 'theirs' : 'mine'));
}
