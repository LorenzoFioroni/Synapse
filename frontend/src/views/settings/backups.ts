// Settings → Backups: the list of backups, Back Up Now, restoring one, the backup folder, and
// whether the notes history is on.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { state } from '../../store.js';
import type { Backup } from '../../types.js';
import { confirmDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml, formatBytes, formatDate, plural } from '../../util.js';
import { installHtml, openVaultHistory } from '../notes/historyView.js';
import { asAdmin } from './confirm.js';
import { flashSaved } from './page.js';
import { bindRemoteBackups, showRemoteBackups } from './remoteBackups.js';

function backupRows(backups: Backup[], source: 'local' | 'remote'): string {
  return backups.map((b) => `<div class="backup-row" data-backup="${escapeHtml(b.name)}" data-source="${source}">
    <div class="b-main"><div class="b-when">${escapeHtml(formatDate(b.created, true))}${b.damaged ? ' · <span class="form-error">cannot be read</span>' : ''}</div>
      <div class="b-what">${b.items != null ? `${plural(b.items, 'item')} · ${plural(b.notes ?? 0, 'note')} · ` : ''}${formatBytes(b.size)}${b.uploaded ? ' · uploaded' : ''}${b.reason && b.reason !== 'manual' && b.reason !== 'scheduled' ? ` · ${escapeHtml(b.reason)}` : ''}</div></div>
    <button class="btn btn-sm" data-restore ${b.damaged ? 'disabled' : ''}>${icon('restore')} Restore…</button></div>`).join('');
}

async function refreshRemoteBackups(view: HTMLElement): Promise<void> {
  const desc = view.querySelector<HTMLElement>('[data-remote-backup-status]')!;
  const list = view.querySelector<HTMLElement>('[data-remote-backup-list]')!;
  desc.textContent = 'Fetching remote backups…';
  list.innerHTML = '';
  try {
    const response = await api.remoteBackups();
    if (!view.isConnected) return;
    list.innerHTML = backupRows(response.backups, 'remote');
    desc.textContent = response.backups.length ? '' : 'No remote backups yet.';
  } catch (err) { desc.textContent = (err as Error).message; }
}

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
export async function refreshBackups(view: HTMLElement, history = true): Promise<void> {
  if (history) refreshHistory(view);
  const desc = view.querySelector<HTMLElement>('[data-backup-desc]')!;
  const list = view.querySelector<HTMLElement>('[data-backup-list]')!;
  try {
    const st = await api.backups();
    if (!view.isConnected) return;
    const last = st.last ? `Last backup ${formatDate(st.last.created, true)}` : 'No backup yet';
    desc.innerHTML = `${escapeHtml(last)} · in <span class="mono">${escapeHtml(st.dir)}</span>`
      + (st.destination === 'remote' ? `<div>${st.delivery.running ? 'Uploading…' : `${st.delivery.pending} pending uploads`}${st.delivery.last_uploaded ? ` · Last upload ${escapeHtml(formatDate(st.delivery.last_uploaded.created, true))}` : ''}</div>` : '')
      + [st.error, st.destination === 'remote' ? st.delivery.error : null, st.delivery.warning].filter(Boolean).map((error) => `<div class="form-error">${escapeHtml(error!)}</div>`).join('');
    list.innerHTML = backupRows(st.backups, 'local');
    const auth = view.querySelector<HTMLElement>('[data-remote-auth-state]');
    if (auth) auth.textContent = st.remote.authorized ? 'Connection saved and authorized. Test the connection to check access.' : st.remote.configured ? 'Connection saved. Connect your account to authorize backups.' : 'Save your remote connection details to get started.';
  } catch (e) { desc.textContent = (e as Error).message; }
}

export function bindBackups(view: HTMLElement): () => void {
  let restoring = false;
  showRemoteBackups(view, state.settings.backup_destination === 'remote');
  void bindRemoteBackups(view, () => { void refreshBackups(view, false); });
  const destination = view.querySelector<HTMLSelectElement>('[data-backup-destination]')!;
  destination.onchange = async () => {
    try {
      const response = await asAdmin(() => api.saveSettings({ backup_destination: destination.value as 'local' | 'remote' }));
      if (!response) { destination.value = state.settings.backup_destination; return; }
      state.settings = response.settings;
      showRemoteBackups(view, state.settings.backup_destination === 'remote');
      if (state.settings.backup_destination === 'remote') void refreshRemoteBackups(view);
      void refreshBackups(view, false);
    } catch (err) { destination.value = state.settings.backup_destination; toastError(err); }
  };
  view.querySelector<HTMLButtonElement>('[data-remote-backup-refresh]')!.onclick = () => { void refreshRemoteBackups(view); };
  if (state.settings.backup_destination === 'remote') void refreshRemoteBackups(view);
  const timer = setInterval(() => { if (view.isConnected && !restoring) void refreshBackups(view, false); }, 4000);
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
  view.querySelector('#set-backups')!.addEventListener('click', async (e) => {
    const btn = (e.target as Element).closest<HTMLButtonElement>('[data-restore]');
    if (!btn || restoring) return;
    const row = btn.closest<HTMLElement>('[data-backup]')!;
    const when = row.querySelector('.b-when')!.textContent;
    if (!(await confirmDialog(`Restore the backup of ${when}?`, {
      title: 'Restore backup', confirm: 'Restore', danger: true,
      detail: 'Your library, settings and notes go back to how they were then. The current state is backed up first, so this can be undone by restoring that backup. PDFs are not changed.',
    }))) return;
    restoring = true;
    btn.disabled = true;
    try {
      const res = await api.restoreBackup(row.dataset.backup!, row.dataset.source as 'local' | 'remote');
      toast(`Backup restored. The state before it is saved as ${res.safety_backup}.`, { type: 'success' });
      setTimeout(() => location.reload(), 800);
    } catch (err) { toastError(err); btn.disabled = false; restoring = false; }
  });
  return () => clearInterval(timer);
}
