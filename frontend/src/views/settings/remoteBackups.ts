// The connection editor owns its draft; status polling must never replace typed credentials.
import { api } from '../../api.js';
import type { RemoteBackupSetup } from '../../types.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml } from '../../util.js';
import { asAdmin } from './confirm.js';
import { bindRemoteCertificate } from './remoteCertificate.js';

const guides: Record<string, string> = {
  drive: 'https://developers.google.com/identity/protocols/oauth2/web-server',
  onedrive: 'https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app',
  dropbox: 'https://www.dropbox.com/developers/apps',
};

/** A row of the connection form, laid out like every other setting. */
function row(id: string, title: string, hint: string, control: string, kind = ''): string {
  return `<div class="setting remote-row${kind === 'stack' ? ' stack' : ''}${kind && kind !== 'stack' ? ` ${kind}` : ''}">
    <div class="s-text"><label class="s-title" for="${id}">${title}</label>${hint ? `<div class="s-desc">${hint}</div>` : ''}</div>
    <div class="s-control">${control}</div></div>`;
}

/** "Port (optional; default 22)" → the title "Port" and the hint "Optional; default 22". */
export function splitLabel(label: string, required: boolean): { title: string; hint: string } {
  const m = /^(.*?)\s*\((.*)\)$/.exec(label);
  const title = escapeHtml(m ? m[1] : label) + (required ? ' <span class="req" aria-hidden="true">*</span>' : '');
  return { title, hint: m ? escapeHtml(m[2].charAt(0).toUpperCase() + m[2].slice(1)) + '.' : '' };
}

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
    <div class="card remote-card">
      ${row('remote-backup-provider', 'Storage provider', '<span data-remote-provider-hint></span>',
        `<select class="select" id="remote-backup-provider" data-remote-provider>${setup.providers.map((p) => `<option value="${p.id}" ${p.id === config.provider ? 'selected' : ''}>${escapeHtml(p.name)}</option>`).join('')}</select>`)}
      <div data-remote-fields></div>
      <div data-remote-certificate></div>
      ${row('remote-backup-folder', 'Destination folder', 'Synapse makes its own subfolder here and only removes its own old backups.',
        `<input class="input mono" id="remote-backup-folder" data-remote-folder value="${escapeHtml(config.folder || '')}" placeholder="Synapse backups (S3: bucket/folder)" spellcheck="false" autocomplete="off">`)}
      ${row('remote-backup-namespace', 'Backup set', 'Recovering on a new installation? Save and connect first, then find and pick the previous set.',
        `<select class="select" id="remote-backup-namespace" data-remote-namespace><option value="${escapeHtml(config.namespace || '')}">${escapeHtml(config.namespace || 'New backup set')}</option></select>
         <button class="btn btn-sm" data-remote-find-sets>Find existing sets</button>`, 'remote-namespace')}
      <div class="setting remote-auth" data-remote-auth-info hidden></div>
      <div class="remote-foot">
        <div class="remote-state"><div class="s-desc" data-remote-auth-state></div><p class="form-error" data-remote-error hidden></p></div>
        <div class="remote-actions">
          <button class="btn btn-ghost" data-remote-retry>Retry uploads</button>
          <button class="btn" data-remote-connect hidden>Connect account</button>
          <button class="btn" data-remote-test>Test connection</button>
          <button class="btn btn-primary" data-remote-save>Save connection</button>
        </div>
      </div>
    </div>`;
  const select = host.querySelector<HTMLSelectElement>('[data-remote-provider]')!;
  const fields = host.querySelector<HTMLElement>('[data-remote-fields]')!;
  const certificateHost = host.querySelector<HTMLElement>('[data-remote-certificate]')!;
  let certificate: ReturnType<typeof bindRemoteCertificate> | undefined;
  function paintFields(): void {
    const provider = setup.providers.find((p) => p.id === select.value)!;
    const same = config.provider === provider.id;
    fields.innerHTML = provider.fields.map(([key, label, secret, required]) => {
      const value = same && !secret ? (key === 'known_hosts' ? config.known_hosts : config.options[key]) || '' : '';
      const stored = same && config.stored_secrets.includes(key);
      const id = `remote-backup-${key}`;
      const choices = provider.choices?.[key];
      const { title, hint } = splitLabel(label, required);
      const keep = stored ? 'Saved; leave blank to keep' : '';
      if (['key_pem', 'known_hosts'].includes(key)) {
        return row(id, title, hint, `<textarea class="input mono" rows="3" id="${id}" data-remote-option="${key}" spellcheck="false" autocomplete="off" placeholder="${keep}">${escapeHtml(value)}</textarea>`, 'stack');
      }
      const control = choices
        ? `<select class="select" id="${id}" data-remote-option="${key}">${choices.map(([choice, text]) => `<option value="${escapeHtml(choice)}" ${choice === value ? 'selected' : ''}>${escapeHtml(text)}</option>`).join('')}</select>`
        : `<input class="input" type="${secret ? 'password' : 'text'}" id="${id}" data-remote-option="${key}" value="${escapeHtml(value)}" spellcheck="false" autocomplete="off" placeholder="${keep}">`;
      if (key === 'pass') {
        return row(id, title, [hint, 'Tick “Already obscured” only when pasting the output of <span class="mono">rclone obscure</span>.'].filter(Boolean).join(' '),
          `${control}<label class="check remote-obscured"><input type="checkbox" data-remote-password-obscured> Already obscured by rclone</label>`, 'remote-pass');
      }
      return row(id, title, hint, control);
    }).join('');
    host.querySelector('[data-remote-provider-hint]')!.textContent = provider.id === 'ftp'
      ? 'Pick the TLS mode your FTP server requires. Folders are relative to your FTP home folder unless they start with /.' : '';
    certificateHost.hidden = !provider.custom_ca;
    certificateHost.replaceChildren();
    certificate = provider.custom_ca ? bindRemoteCertificate(certificateHost, same ? config.trusted_certificate : null) : undefined;
    const info = host.querySelector<HTMLElement>('[data-remote-auth-info]')!;
    info.hidden = !provider.oauth;
    info.innerHTML = `<div class="s-desc">Register a Web application with your provider and enter its application credentials above. Set its redirect URI to <code>${escapeHtml(setup.callback)}</code>, then choose Connect account. <a href="${guides[provider.id] || '#'}" target="_blank" rel="noopener noreferrer">Provider application setup</a>.
      ${provider.id === 'onedrive' ? 'Allow delegated Files.ReadWrite, User.Read and offline_access permissions.' : ''}
      ${provider.id === 'drive' ? 'Enable the Google Drive API and publish the consent app for lasting access.' : ''}</div>`;
    host.querySelector<HTMLElement>('[data-remote-connect]')!.hidden = !provider.oauth;
  }
  select.onchange = paintFields;
  paintFields();
  const error = host.querySelector<HTMLElement>('[data-remote-error]')!;
  async function save(): Promise<boolean> {
    const options: Record<string, string> = {};
    host.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('[data-remote-option]').forEach((el) => { options[el.dataset.remoteOption!] = el.value; });
    const data = { provider: select.value, folder: host.querySelector<HTMLInputElement>('[data-remote-folder]')!.value, namespace: host.querySelector<HTMLSelectElement>('[data-remote-namespace]')!.value, options, password_obscured: host.querySelector<HTMLInputElement>('[data-remote-password-obscured]')?.checked || false, ca_certificate: await certificate?.read() };
    if (data.provider !== select.value) throw new Error('The storage provider changed. Save the connection again.');
    const response = await asAdmin(() => api.saveRemoteBackup(data));
    if (!response) return false;
    setup.configuration = response.configuration;
    Object.assign(config, response.configuration);
    certificate?.saved(config.trusted_certificate);
    const namespace = host.querySelector<HTMLSelectElement>('[data-remote-namespace]')!;
    if (!namespace.value) namespace.innerHTML = `<option value="${escapeHtml(config.namespace!)}">${escapeHtml(config.namespace!)}</option>`;
    host.querySelectorAll<HTMLInputElement>('[data-remote-option][type="password"]').forEach((el) => { el.value = ''; el.placeholder = 'Saved; leave blank to keep'; });
    host.querySelector<HTMLTextAreaElement>('[data-remote-option="key_pem"]')?.setAttribute('placeholder', 'Saved; leave blank to keep');
    const key = host.querySelector<HTMLTextAreaElement>('[data-remote-option="key_pem"]');
    if (key) key.value = '';
    const obscured = host.querySelector<HTMLInputElement>('[data-remote-password-obscured]');
    if (obscured) obscured.checked = false;
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
