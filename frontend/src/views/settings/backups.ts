// Settings → Backups: the list of backups, Back Up Now, restoring one, the backup folder, and
// whether the notes history is on.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { state } from '../../store.js';
import { confirmDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml, formatBytes, formatDate, plural } from '../../util.js';
import { installHtml, openVaultHistory } from '../notes/historyView.js';
import { asAdmin } from './confirm.js';
import { flashSaved } from './page.js';

/** Whether the notes history is on (it needs Git), in the open Settings page ``view``. */
async function refreshHistory(view: HTMLElement): Promise<void> {
  const desc = view.querySelector<HTMLElement>('[data-history-desc]');
  if (!desc) return;
  try {
    const st = await api.historyStatus();
    if (!view.isConnected) return;
    desc.innerHTML = st.enabled
      ? `Every version of your notes’ text is kept, with who changed it (you, an AI app or another program), in <span class="mono">${escapeHtml(st.folder)}</span> (Git ${escapeHtml(st.git || '')}). Backups include it.`
        + (st.error ? `<div class="form-error">${escapeHtml(st.error)}</div>` : '')
      : `<span class="form-error">${installHtml(st)}</span> Until then only the changes AI apps make are kept.`;
    view.querySelector<HTMLButtonElement>('[data-history-open]')!.hidden = !st.enabled;
  } catch (e) { desc.textContent = (e as Error).message; }
}

/** Show the backups (and where they are) in the open Settings page ``view``. */
export async function refreshBackups(view: HTMLElement): Promise<void> {
  refreshHistory(view);
  const desc = view.querySelector<HTMLElement>('[data-backup-desc]')!;
  const list = view.querySelector<HTMLElement>('[data-backup-list]')!;
  try {
    const st = await api.backups();
    if (!view.isConnected) return;
    const last = st.last ? `Last backup ${formatDate(st.last.created, true)}` : 'No backup yet';
    desc.innerHTML = `${escapeHtml(last)} · in <span class="mono">${escapeHtml(st.dir)}</span>`
      + (st.error ? `<div class="form-error">${escapeHtml(st.error)}</div>` : '');
    list.innerHTML = st.backups.length ? st.backups.map((b) => `<div class="backup-row" data-backup="${escapeHtml(b.name)}">
        <div class="b-main"><div class="b-when">${escapeHtml(formatDate(b.created, true))}${b.damaged ? ' · <span class="form-error">cannot be read</span>' : ''}</div>
          <div class="b-what">${b.items != null ? `${plural(b.items, 'item')} · ${plural(b.notes ?? 0, 'note')} · ` : ''}${formatBytes(b.size)}${b.reason && b.reason !== 'manual' && b.reason !== 'scheduled' ? ` · ${escapeHtml(b.reason)}` : ''}</div></div>
        <button class="btn btn-sm" data-restore ${b.damaged ? 'disabled' : ''}>${icon('restore')} Restore…</button></div>`).join('')
      : '';
  } catch (e) { desc.textContent = (e as Error).message; }
}

export function bindBackups(view: HTMLElement): void {
  view.querySelector<HTMLButtonElement>('[data-history-open]')!.onclick = () => openVaultHistory();
  const bdir = view.querySelector<HTMLInputElement>('[data-backup-dir]')!;
  bdir.addEventListener('change', async () => {
    const errEl = view.querySelector<HTMLElement>('[data-backup-dir-error]')!;
    errEl.hidden = true;
    try {
      // a passkey confirmation first when this is not the computer Synapse runs on
      const res = await asAdmin(() => api.saveSettings({ backup_dir: bdir.value.trim() }));
      if (!res) {
        bdir.value = state.settings.backup_dir;
        throw new Error('Not saved: changing the backup folder from another device needs your passkey.');
      }
      state.settings = res.settings;
      flashSaved(bdir.closest('.setting'));
      refreshBackups(view);
    } catch (e) { errEl.textContent = (e as Error).message; errEl.hidden = false; }
  });
  view.querySelector<HTMLButtonElement>('[data-backup-now]')!.onclick = async (e) => {
    const b = e.currentTarget as HTMLButtonElement;
    b.disabled = true;
    try { await api.backupNow(); toast('Backup made', { type: 'success' }); } catch (err) { toastError(err); }
    b.disabled = false;
    refreshBackups(view);
  };
  view.querySelector('[data-backup-list]')!.addEventListener('click', async (e) => {
    const btn = (e.target as Element).closest<HTMLButtonElement>('[data-restore]');
    if (!btn) return;
    const row = btn.closest<HTMLElement>('[data-backup]')!;
    const when = row.querySelector('.b-when')!.textContent;
    if (!(await confirmDialog(`Restore the backup of ${when}?`, {
      title: 'Restore backup', confirm: 'Restore', danger: true,
      detail: 'Your library, settings and notes go back to how they were then. The current state is backed up first, so this can be undone by restoring that backup. PDFs are not changed.',
    }))) return;
    btn.disabled = true;
    try {
      const res = await api.restoreBackup(row.dataset.backup!);
      toast(`Backup restored. The state before it is saved as ${res.safety_backup}.`, { type: 'success' });
      setTimeout(() => location.reload(), 800);
    } catch (err) { toastError(err); btn.disabled = false; }
  });
}
