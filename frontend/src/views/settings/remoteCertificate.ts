import type { RemoteBackupConfiguration } from '../../types.js';

const MAX_CA_BYTES = 1024 * 1024;

export function bindRemoteCertificate(host: HTMLElement, savedCertificate: RemoteBackupConfiguration['trusted_certificate']) {
  host.innerHTML = `<div style="margin-top:10px">
    <label for="remote-backup-ca">Trusted certificate (optional)</label>
    <input class="input" type="file" id="remote-backup-ca" data-remote-ca accept=".pem,.crt,.cer,.txt">
    <p class="s-desc">Upload a PEM certificate or bundle such as ca-certificates.crt (up to 1 MiB). Use certificates from a source you trust. Applies only to this backup connection; server identity checks remain enabled.</p>
    <p class="s-desc" data-remote-ca-state role="status" style="overflow-wrap:anywhere"></p>
    <button class="btn btn-sm" type="button" data-remote-ca-remove>Remove certificate</button>
  </div>`;
  const input = host.querySelector<HTMLInputElement>('[data-remote-ca]')!;
  const status = host.querySelector<HTMLElement>('[data-remote-ca-state]')!;
  const remove = host.querySelector<HTMLButtonElement>('[data-remote-ca-remove]')!;
  let removed = false;
  function paint(): void {
    const file = input.files?.[0];
    const saved = savedCertificate;
    status.textContent = file ? `${file.name} selected. Save connection to upload it.`
      : removed ? 'Certificate will be removed when you save the connection.'
      : saved ? `Saved: ${saved.count} certificate${saved.count === 1 ? '' : 's'}. Bundle SHA-256: ${saved.sha256}`
      : 'No custom certificate. Uses the server’s default trusted certificates.';
    remove.disabled = !file && (removed || !saved);
  }
  input.onchange = () => { removed = false; paint(); };
  remove.onclick = () => { input.value = ''; removed = true; paint(); };
  paint();
  return {
    async read(): Promise<string | null | undefined> {
      if (removed) return null;
      const file = input.files?.[0];
      if (!file) return undefined;
      if (!file.size || file.size > MAX_CA_BYTES) throw new Error('Upload a PEM certificate or certificate bundle up to 1 MiB.');
      return file.text();
    },
    saved(value: RemoteBackupConfiguration['trusted_certificate']): void { savedCertificate = value; input.value = ''; removed = false; paint(); },
  };
}
