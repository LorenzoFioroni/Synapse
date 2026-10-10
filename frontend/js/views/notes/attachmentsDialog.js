// The attachments of notes (images in the notes folder's attachments/): which notes show each
// one, inserting one into the open note (an image can be used by several notes), and the
// attachment trash — images no note uses any more, restorable until they are deleted for good
// (Settings → General → Empty the notes trash). See backend/notes/attachments.py.

import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { openDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import { copyText, escapeHtml as esc, formatBytes, formatDate, plural } from '../../util.js';

export const RETENTION_LABELS = { never: 'never', '1d': 'after 1 day', '1w': 'after 1 week', '2w': 'after 2 weeks',
  '1m': 'after 1 month', '6m': 'after 6 months' };

const src = (a) => `/api/notes/${a.path.split('/').map(encodeURIComponent).join('/')}`;   // attachments/<name>
const trashSrc = (t) => `/api/notes/attachments/trash/${encodeURIComponent(t.trash_id)}/${t.name.split('/').map(encodeURIComponent).join('/')}`;
const stem = (p) => p.replace(/^\.trash\/[^/]+\//, '').replace(/\.md$/i, '');

/**
 * @param insert (markdown) => boolean — puts the image into the open note; false if none is open
 * @param openNote (path) => void
 */
export async function openAttachments({ insert, openNote }) {
  let data;
  try { data = await api.noteAttachments(); } catch (e) { toastError(e); return; }
  let tab = 'all';
  await openDialog({
    title: 'Attachments',
    subtitle: 'Images in your notes, stored in the attachments folder of your notes. Ones no note uses any more go to the trash'
      + (data.retention === 'never' ? '.' : ` and are deleted ${RETENTION_LABELS[data.retention] || ''}.`),
    wide: true,
    render: (b) => {
      const paint = () => {
        const unused = data.attachments.filter((a) => !a.used_by.length);
        const rows = tab === 'trash' ? data.trash : tab === 'unused' ? unused : data.attachments;
        b.innerHTML = `<div class="segmented att-tabs">
            <button data-tab="all" class="${tab === 'all' ? 'active' : ''}">All <span class="count">${data.attachments.length}</span></button>
            <button data-tab="unused" class="${tab === 'unused' ? 'active' : ''}" title="Not shown by any note yet (just added, or about to go to the trash)">Not used <span class="count">${unused.length}</span></button>
            <button data-tab="trash" class="${tab === 'trash' ? 'active' : ''}">Trash <span class="count">${data.trash.length}</span></button>
          </div>
          <div class="att-grid">${rows.length ? rows.map((a, i) => (tab === 'trash' ? trashCard(a, i) : card(a, i))).join('')
            : `<p class="muted att-empty">${tab === 'trash' ? 'The attachment trash is empty.' : tab === 'unused' ? 'Every attachment is used by a note.' : 'No attachments yet. Paste or drop an image into a note.'}</p>`}</div>`;
        b.querySelectorAll('[data-tab]').forEach((x) => { x.onclick = () => { tab = x.dataset.tab; paint(); }; });
        b.querySelectorAll('[data-act]').forEach((x) => { x.onclick = () => act(x, rows[Number(x.dataset.i)]); });
      };
      const act = async (btn, a) => {
        const md = `![](${a.path})`;
        if (btn.dataset.act === 'insert') {
          if (insert(md)) toast('Image added to the note.', { type: 'success' });
          else toast('Open a note first, then insert the image.');
        } else if (btn.dataset.act === 'copy') {
          await copyText(md);
          toast('Copied — paste it into any note.');
        } else if (btn.dataset.act === 'open') {
          openNote(btn.dataset.path);
        } else if (btn.dataset.act === 'restore') {
          try {
            await api.restoreNoteAttachment(a.trash_id, a.name);
            data = await api.noteAttachments();
            toast('Restored. Use it in a note, or it goes back to the trash in a while.');
            paint();
          } catch (e) { toastError(e); }
        } else if (btn.dataset.act === 'delete') {
          try {
            await api.deleteNoteAttachment(a.trash_id, a.name);
            data.trash = data.trash.filter((t) => t !== a);
            paint();
          } catch (e) { toastError(e); }
        }
      };
      paint();
    },
    buttons: [{ label: 'Close', kind: 'primary' }],
  });
}

function card(a, i) {
  const notes = a.used_by.map((p) => (p.startsWith('.trash/')
    ? `<span class="muted" title="In the notes trash">${esc(stem(p))} (deleted)</span>`
    : `<button class="btn-link" data-act="open" data-path="${esc(p)}" data-i="${i}">${esc(stem(p))}</button>`));
  return `<div class="att-card">
    <div class="att-thumb"><img src="${src(a)}" alt="" loading="lazy"></div>
    <div class="att-name mono" title="${esc(a.path)}">${esc(a.name)}</div>
    <div class="att-meta">${formatBytes(a.size)} · ${a.used_by.length ? `in ${plural(a.used_by.length, 'note')}` : '<span class="warn">not used</span>'}</div>
    ${notes.length ? `<div class="att-notes">${notes.join(', ')}</div>` : ''}
    <div class="att-actions">
      <button class="btn btn-sm" data-act="insert" data-i="${i}" title="Insert into the open note">${icon('plus', 'icon-sm')} Insert</button>
      <button class="btn btn-sm btn-ghost" data-act="copy" data-i="${i}" title="Copy the Markdown to paste in a note">${icon('copy', 'icon-sm')}</button>
    </div></div>`;
}

function trashCard(t, i) {
  return `<div class="att-card trashed">
    <div class="att-thumb"><img src="${trashSrc(t)}" alt="" loading="lazy"></div>
    <div class="att-name mono" title="${esc(t.name)}">${esc(t.name)}</div>
    <div class="att-meta">${formatBytes(t.size)} · moved ${esc(formatDate(t.trashed_at))}</div>
    <div class="att-meta">${t.deletes_at ? `deleted for good on ${esc(formatDate(t.deletes_at))}` : 'kept until you delete it'}</div>
    <div class="att-actions">
      <button class="btn btn-sm" data-act="restore" data-i="${i}">${icon('restore', 'icon-sm')} Restore</button>
      <button class="btn btn-sm btn-ghost danger" data-act="delete" data-i="${i}" title="Delete for good now">${icon('trash', 'icon-sm')}</button>
    </div></div>`;
}
