// The connection editor owns its draft; status polling must never replace typed credentials.
import { api } from '../../api.js';
import type { RemoteBackupSetup } from '../../types.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml } from '../../util.js';
import { asAdmin } from './confirm.js';

const guides: Record<string, string> = {
  drive: 'https://developers.google.com/identity/protocols/oauth2/web-server',
  onedrive: 'https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app',
  dropbox: 'https://www.dropbox.com/developers/apps',
};

export function showRemoteBackups(view: HTMLElement, remote: boolean): void {
  view.querySelector<HTMLElement>('[data-remote-backup]')!.hidden = !remote;
  view.querySelector<HTMLElement>('[data-remote-backup-archives]')!.hidden = !remote;
}

export async function bindRemoteBackups(view: HTMLElement, changed: () => void): Promise<void> {
  const host = view.querySelector<HTMLElement>('[data-remote-backup-form]')!;
  let setup: RemoteBackupSetup;
  try {
    setup = await api.remoteBackupSetup();
    if (!view.isConnected) return;
  } catch (err) { host.textContent = (err as Error).message; return; }
  const config = setup.configuration;
  host.innerHTML = `${!config.available ? '<p class="form-error">rclone is missing on this server. Use the Synapse Docker image, which includes it.</p>' : ''}
    <label>Storage provider <select class="select" data-remote-provider>${setup.providers.map((p) => `<option value="${p.id}" ${p.id === config.provider ? 'selected' : ''}>${escapeHtml(p.name)}</option>`).join('')}</select></label>
    <div data-remote-fields></div>
    <label>Destination folder <input class="input mono" data-remote-folder value="${escapeHtml(config.folder || '')}" placeholder="Synapse backups (S3: bucket/folder)"></label>
    <p class="s-desc">Synapse creates its own subfolder here and only removes its own old backups. Local and remote copies each keep the selected number of backups.</p>
    <div><label>Backup set <select class="select" data-remote-namespace><option value="${escapeHtml(config.namespace || '')}">${escapeHtml(config.namespace || 'New backup set')}</option></select></label>
      <button class="btn btn-sm" data-remote-find-sets>Find existing backup sets</button>
      <p class="s-desc">When recovering on a new installation, save and connect your account, then find and select the previous backup set.</p></div>
    <div class="s-desc" data-remote-auth-info hidden></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
      <button class="btn" data-remote-save>Save connection</button>
      <button class="btn" data-remote-connect hidden>Connect account</button>
      <button class="btn" data-remote-test>Test connection</button>
      <button class="btn" data-remote-retry>Retry uploads</button>
    </div><p class="s-desc" data-remote-auth-state></p><p class="form-error" data-remote-error hidden></p>`;
  const select = host.querySelector<HTMLSelectElement>('[data-remote-provider]')!;
  const fields = host.querySelector<HTMLElement>('[data-remote-fields]')!;
  function paintFields(): void {
    const provider = setup.providers.find((p) => p.id === select.value)!;
    const same = config.provider === provider.id;
    fields.innerHTML = provider.fields.map(([key, label, secret, required]) => {
      const value = same && !secret ? (key === 'known_hosts' ? config.known_hosts : config.options[key]) || '' : '';
      const stored = same && config.stored_secrets.includes(key);
      const id = `remote-backup-${key}`;
      const control = ['key_pem', 'known_hosts'].includes(key)
        ? `<textarea class="input mono" rows="3" id="${id}" data-remote-option="${key}" spellcheck="false" autocomplete="off" placeholder="${stored ? 'Saved; leave blank to keep' : ''}">${escapeHtml(value)}</textarea>`
        : `<input class="input" type="${secret ? 'password' : 'text'}" id="${id}" data-remote-option="${key}" value="${escapeHtml(value)}" spellcheck="false" autocomplete="off" placeholder="${stored ? 'Saved; leave blank to keep' : ''}">`;
      return `<div style="margin-top:10px"><label for="${id}">${escapeHtml(label)}${required ? ' *' : ''}</label>${control}</div>`;
    }).join('');
    const info = host.querySelector<HTMLElement>('[data-remote-auth-info]')!;
    info.hidden = !provider.oauth;
    info.innerHTML = `Register a Web application with your provider and enter its application credentials above. Set its redirect URI to <code>${escapeHtml(setup.callback)}</code>, then choose Connect account. <a href="${guides[provider.id] || '#'}" target="_blank" rel="noopener noreferrer">Provider application setup</a>.
      ${provider.id === 'onedrive' ? 'Allow delegated Files.ReadWrite, User.Read and offline_access permissions.' : ''}
      ${provider.id === 'drive' ? 'Enable the Google Drive API and publish the consent app for lasting access.' : ''}`;
    host.querySelector<HTMLElement>('[data-remote-connect]')!.hidden = !provider.oauth;
  }
  select.onchange = paintFields;
  paintFields();
  const error = host.querySelector<HTMLElement>('[data-remote-error]')!;
  async function save(): Promise<boolean> {
    const options: Record<string, string> = {};
    host.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('[data-remote-option]').forEach((el) => { options[el.dataset.remoteOption!] = el.value; });
    const response = await asAdmin(() => api.saveRemoteBackup({ provider: select.value, folder: host.querySelector<HTMLInputElement>('[data-remote-folder]')!.value, namespace: host.querySelector<HTMLSelectElement>('[data-remote-namespace]')!.value, options }));
    if (!response) return false;
    setup.configuration = response.configuration;
    Object.assign(config, response.configuration);
    const namespace = host.querySelector<HTMLSelectElement>('[data-remote-namespace]')!;
    if (!namespace.value) namespace.innerHTML = `<option value="${escapeHtml(config.namespace!)}">${escapeHtml(config.namespace!)}</option>`;
    host.querySelectorAll<HTMLInputElement>('[data-remote-option][type="password"]').forEach((el) => { el.value = ''; el.placeholder = 'Saved; leave blank to keep'; });
    host.querySelector<HTMLTextAreaElement>('[data-remote-option="key_pem"]')?.setAttribute('placeholder', 'Saved; leave blank to keep');
    const key = host.querySelector<HTMLTextAreaElement>('[data-remote-option="key_pem"]');
    if (key) key.value = '';
    changed();
    return true;
  }
  async function action(button: HTMLButtonElement, work: () => Promise<void>): Promise<void> {
    button.disabled = true;
    error.hidden = true;
    try { await work(); }
    catch (err) { error.textContent = (err as Error).message; error.hidden = false; }
    finally { button.disabled = false; }
  }
  host.querySelector<HTMLButtonElement>('[data-remote-save]')!.onclick = (event) => { void action(event.currentTarget as HTMLButtonElement, async () => { if (await save()) toast('Remote connection saved', { type: 'success' }); }); };
  host.querySelector<HTMLButtonElement>('[data-remote-find-sets]')!.onclick = (event) => { void action(event.currentTarget as HTMLButtonElement, async () => {
    const response = await api.remoteBackupSets();
    const namespace = host.querySelector<HTMLSelectElement>('[data-remote-namespace]')!;
    namespace.innerHTML = [...new Set([config.namespace!, ...response.sets])].map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
    toast(response.sets.length ? 'Choose a backup set, then save the connection' : 'No existing backup sets found');
  }); };
  host.querySelector<HTMLButtonElement>('[data-remote-test]')!.onclick = (event) => { void action(event.currentTarget as HTMLButtonElement, async () => {
    if (!await save()) return;
    if (await asAdmin(() => api.testRemoteBackup()) !== null) toast('Upload, download and deletion tested successfully', { type: 'success' });
  }); };
  host.querySelector<HTMLButtonElement>('[data-remote-connect]')!.onclick = (event) => {
    // Reserve the tab in the click handler; browsers block a popup opened after an async request.
    const popup = window.open('about:blank', '_blank');
    if (popup) popup.opener = null;
    void action(event.currentTarget as HTMLButtonElement, async () => {
      try {
        if (!await save()) { popup?.close(); return; }
        const response = await asAdmin(() => api.authorizeRemoteBackup());
        if (!response) { popup?.close(); return; }
        if (popup) popup.location.href = response.url;
        else throw new Error('Allow popups for Synapse, then choose Connect account again.');
      } catch (err) { popup?.close(); throw err; }
    });
  };
  host.querySelector<HTMLButtonElement>('[data-remote-retry]')!.onclick = async () => {
    try { await api.retryRemoteBackups(); changed(); toast('Upload retry requested'); } catch (err) { toastError(err); }
  };
}
