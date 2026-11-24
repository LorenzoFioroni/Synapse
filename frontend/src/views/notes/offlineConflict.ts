// The choice for a note whose offline edits could not be merged with what changed in Synapse
// meanwhile, or which was deleted there (offlineSync.ts, docs/offline.md). Shown when the note is
// opened, or at once when it is open. Nothing was written in Synapse before this choice.
import { api } from '../../api.js';
import { localNote } from '../../offlineNotes.js';
import { resolveNote } from '../../offlineSync.js';
import { openDialog } from '../../ui/dialog.js';
import { toastError } from '../../ui/toast.js';
import { diffHtml } from './aiEdits.js';
import type { NoteSession } from './noteSession.js';

const showing = new Set<string>();

export async function offlineConflictDialog(path: string, session: NoteSession): Promise<void> {
  const c = localNote(path)?.conflict;
  if (!c || showing.has(path)) return;
  showing.add(path);
  try {
    let choice: string | null;
    if (c.status === 'deleted') {
      choice = await openDialog({
        title: 'This note was deleted while you were offline',
        subtitle: 'Your offline edits are kept here; nothing has been written to Synapse.',
        dismissable: false,
        render: (b) => { b.innerHTML = '<p style="margin:0">Save your offline version again under the same name, or let it go.</p>'; },
        buttons: [{ label: 'Discard my offline edits', value: 'theirs' }, { label: 'Save it again', kind: 'primary', value: 'mine' }],
      }) as string | null;
    } else {
      choice = await openDialog({
        title: 'Offline edits and changes in Synapse overlap',
        subtitle: 'This note was changed in Synapse while you edited it offline, in the same places. Nothing has been overwritten.',
        wide: true,
        dismissable: false,
        render: (b) => {
          b.innerHTML = `<div class="diff-legend"><span class="d-del">− only in Synapse</span><span class="d-add">+ only in your offline version</span></div>${diffHtml(c.current ?? '', c.mine)}`
            + '<p class="muted" style="margin:10px 0 0"><b>Fix by hand</b> puts both in the note, between Git’s conflict markers (<span class="mono">&lt;&lt;&lt;&lt;&lt;&lt;&lt;</span> … <span class="mono">&gt;&gt;&gt;&gt;&gt;&gt;&gt;</span>), for you to edit.</p>';
        },
        buttons: [
          { label: 'Keep the version in Synapse', value: 'theirs' },
          { label: 'Keep my offline version', value: 'mine' },
          { label: 'Fix by hand', kind: 'primary', value: 'merge' },
        ],
      }) as string | null;
    }
    const pick = (choice === 'mine' || choice === 'merge' ? choice : 'theirs') as 'merge' | 'mine' | 'theirs';
    const r = await resolveNote(path, pick);
    if (r.deleted) {
      if (pick === 'theirs') return;
      const res = await api.recreateNote(path, r.text);
      session.adopt({ text: r.text, hash: res.hash }, r.text);
      return;
    }
    session.adopt({ text: c.current ?? '', hash: r.hash }, r.text);
  } catch (e) { toastError(e); } finally { showing.delete(path); }
}
