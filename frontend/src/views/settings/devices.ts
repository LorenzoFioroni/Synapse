// Settings → Devices: pairing an iPad or phone (a one-time code shown as a QR code) and the list
// of paired devices. See backend/access/devices.py.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { confirmDialog, promptDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import type { DevicesOverview, Pairing } from '../../types.js';
import { escapeHtml, formatDate } from '../../util.js';

/** The pairing code being shown: which address is picked, and the devices paired before it. */
let pairing: (Pick<Pairing, 'links' | 'expires'> & { index: number; known: Set<string>; tick: number;
  timer?: ReturnType<typeof setInterval> }) | null = null;
let overview: DevicesOverview | null = null;   // last answer of GET /api/devices

function stopPairing(): void {
  if (pairing) clearInterval(pairing.timer);
  pairing = null;
}

/** Stop showing the pairing code and void it on the server, so the link stops working too. */
export function cancelPairing(): void {
  if (!pairing) return;
  stopPairing();
  api.cancelPairing().catch(toastError);
}

function relTime(iso: string | null): string {
  if (!iso) return 'never';
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return formatDate(iso, true);
}

function renderPairing(box: HTMLElement): void {
  if (!pairing) return;
  const now = Date.now() / 1000;
  const left = Math.max(0, Math.round(pairing.expires - now));
  if (left <= 0) {
    stopPairing();
    box.innerHTML = `<div class="s-desc">The pairing code expired.</div>
      <div><button class="btn" data-pair-new>${icon('plus')} Add a Device</button></div>`;
    return;
  }
  const link = pairing.links[pairing.index] || pairing.links[0];
  const timeLeft = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
  const choices = pairing.links.length > 1 ? `<div class="pair-addrs">Address: ${pairing.links.map((l, i) =>
    `<button class="token${i === pairing!.index ? ' active' : ''}" data-pair-addr="${i}">${escapeHtml(l.label)}</button>`).join(' ')}</div>` : '';
  box.innerHTML = `<div class="pair-box">
      ${link.qr ? `<div class="pair-qr">${link.qr}</div>`
        : `<div class="pair-qr pair-qr-missing">No QR code: the <span class="mono">segno</span> package is missing.
          Run <span class="mono">pip install segno</span> and restart Synapse — or use the link.</div>`}
      <div class="pair-text">
        <div class="s-title">Scan with the other device's camera</div>
        <div class="s-desc">Or open this link on it: <span class="mono pair-url">${escapeHtml(link.url.split('#')[0])}…</span>
          <button class="btn btn-sm" data-pair-copy>${icon('copy')} Copy Link</button></div>
        ${choices}
        <div class="s-desc">If the other device can't open the page, try another address. Both devices must be on the same network.</div>
        <div class="s-desc">The code works once and expires in <b data-pair-left>${timeLeft}</b>.</div>
        <div><button class="btn btn-sm" data-pair-cancel>Cancel</button></div>
      </div></div>`;
}

/** Show the paired devices (and how to add one) in the open Settings page ``view``. */
export async function refreshDevices(view: HTMLElement): Promise<void> {
  const add = view.querySelector<HTMLElement>('[data-device-add]')!;
  const list = view.querySelector<HTMLElement>('[data-device-list]')!;
  const desc = view.querySelector<HTMLElement>('[data-devices-desc]')!;
  let st: DevicesOverview;
  try { st = await api.devices(); } catch (e) { desc.textContent = (e as Error).message; return; }
  if (!view.isConnected) return;
  if (pairing) {
    const known = pairing.known;
    const fresh = st.devices.filter((d) => !known.has(d.id));
    if (fresh.length) {
      stopPairing();
      toast(`${fresh.map((d) => d.name).join(', ')} paired`, { type: 'success' });
    }
  }
  if (!pairing) {
    if (!st.lan) {
      add.innerHTML = `<div class="s-title">Not reachable from other devices</div>
        <div class="s-desc">Synapse only answers this computer. To use it from an iPad or a phone, stop it and start it again with
          <span class="mono">python server.py --lan</span>, then come back here.</div>`;
    } else if (!st.this_computer) {
      add.innerHTML = `<div class="s-desc">New devices are added on the computer running Synapse (Settings → Devices → Add a Device).</div>`;
    } else {
      const where = st.addresses.length ? ` Other devices reach it at <span class="mono">${escapeHtml(st.addresses[0].url)}</span>.` : '';
      add.innerHTML = `<div style="display:flex;align-items:center;gap:12px"><div class="s-text"><div class="s-title">Add a device</div>
          <div class="s-desc">Shows a QR code to scan with the iPad or phone.${where}</div></div>
          <div class="s-control"><button class="btn" data-pair-new>${icon('plus')} Add a Device</button></div></div>`;
    }
  }
  desc.textContent = st.devices.length ? '' : 'No devices yet.';
  // Only the computer running Synapse manages other devices; a device manages only itself.
  list.innerHTML = st.devices.map((d) => `<div class="backup-row" data-device="${escapeHtml(d.id)}">
      <div class="b-main"><div class="b-when">${escapeHtml(d.name)}${d.id === st.current ? ' <span class="device-this">This device</span>' : ''}</div>
        <div class="b-what">Paired ${escapeHtml(formatDate(d.created, true))} · last used ${escapeHtml(relTime(d.last_seen))}${d.last_addr ? ` from ${escapeHtml(d.last_addr)}` : ''}</div></div>
      ${st.this_computer || d.id === st.current ? `<button class="btn btn-sm" data-device-rename>${icon('edit')} Rename…</button>
      <button class="btn btn-sm" data-device-remove>${d.id === st.current ? 'Sign Out' : 'Remove…'}</button>` : ''}</div>`).join('');
  overview = st;
}

async function startPairing(view: HTMLElement): Promise<void> {
  const box = view.querySelector<HTMLElement>('[data-device-add]')!;
  try {
    const res = await api.newPairing();
    const known = new Set((overview?.devices || []).map((d) => d.id));
    stopPairing();
    const p: NonNullable<typeof pairing> = { links: res.links, expires: res.expires, index: 0, known, tick: 0 };
    pairing = p;
    renderPairing(box);
    p.timer = setInterval(() => {
      if (!view.isConnected || pairing !== p) return;
      const left = Math.max(0, Math.round(p.expires - Date.now() / 1000));
      const el = box.querySelector('[data-pair-left]');
      if (left <= 0 || !el) { renderPairing(box); return; }
      el.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
      if (++p.tick % 3 === 0) refreshDevices(view);   // notice when the device has paired
    }, 1000);
  } catch (e) { toastError(e); }
}

export function bindDevices(view: HTMLElement): void {
  const devAdd = view.querySelector<HTMLElement>('[data-device-add]')!;
  devAdd.addEventListener('click', async (e) => {
    const target = e.target as Element;
    if (target.closest('[data-pair-new]')) startPairing(view);
    else if (target.closest('[data-pair-cancel]')) { cancelPairing(); refreshDevices(view); }
    else if (target.closest('[data-pair-addr]') && pairing) {
      pairing.index = Number(target.closest<HTMLElement>('[data-pair-addr]')!.dataset.pairAddr);
      renderPairing(devAdd);
    } else if (target.closest('[data-pair-copy]') && pairing) {
      const link = pairing.links[pairing.index] || pairing.links[0];
      try { await navigator.clipboard.writeText(link.url); toast('Link copied — it works once', { type: 'success' }); } catch (err) { toastError(err); }
    }
  });
  view.querySelector('[data-device-list]')!.addEventListener('click', async (e) => {
    const target = e.target as Element;
    const row = target.closest<HTMLElement>('[data-device]');
    if (!row) return;
    const id = row.dataset.device!;
    const dev = (overview?.devices || []).find((d) => d.id === id);
    if (!dev) return;
    const self = id === overview?.current;
    if (target.closest('[data-device-rename]')) {
      const name = await promptDialog('Name', { title: 'Rename device', value: dev.name, confirm: 'Rename' });
      if (!name || !name.trim()) return;
      try { await api.renameDevice(id, name.trim()); } catch (err) { toastError(err); }
      refreshDevices(view);
    } else if (target.closest('[data-device-remove]')) {
      const ok = await confirmDialog(self ? 'Sign out this device?' : `Remove ${dev.name}?`, {
        title: self ? 'Sign out' : 'Remove device', confirm: self ? 'Sign Out' : 'Remove', danger: true,
        detail: self ? 'To use the library here again, pair this device again from the computer running Synapse.'
          : 'It can no longer open the library until it is paired again.' });
      if (!ok) return;
      try { await api.removeDevice(id); } catch (err) { toastError(err); return; }
      if (self) { location.reload(); return; }
      refreshDevices(view);
    }
  });
}
