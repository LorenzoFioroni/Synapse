import type { RemoteBackupConfiguration } from '../../types.js';

const MAX_CA_BYTES = 1024 * 1024;

export function bindRemoteCertificate(host: HTMLElement, savedCertificate: RemoteBackupConfiguration['trusted_certificate']) {
  host.innerHTML = `<div class="setting remote-row remote-cert">
    <div class="s-text"><label class="s-title" for="remote-backup-ca">Trusted certificate</label>
      <div class="s-desc">Optional. A PEM certificate or bundle such as ca-certificates.crt (up to 1 MiB), from a source you trust. Only for this connection; server identity checks stay on.</div>
      <div class="s-desc remote-ca-state" data-remote-ca-state role="status"></div></div>
    <div class="s-control"><label class="btn btn-sm file-btn">Choose File…<input type="file" id="remote-backup-ca" data-remote-ca accept=".pem,.crt,.cer,.txt"></label>
      <button class="btn btn-sm" type="button" data-remote-ca-remove>Remove</button></div>
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
