// Settings → Devices → Passkeys: add a passkey, so that devices can sign in without a pairing code
// (frontend/signin.html), and remove one. See backend/access/passkeys.py.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { confirmDialog, promptDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import type { PasskeysOverview } from '../../types.js';
import { escapeHtml, formatDate } from '../../util.js';
import { asAdmin, b64url, fromB64url } from './confirm.js';
import { relTime } from './devices.js';

let overview: PasskeysOverview | null = null;

/** Why this browser cannot make a passkey here, or '' when it can. */
function browserProblem(): string {
  if (!window.isSecureContext) return 'This page is not served over HTTPS.';
  if (!('PublicKeyCredential' in window)) return 'This browser does not support passkeys.';
  return '';
}

/** Show the passkeys (and whether one can be added here) in the open Settings page ``view``. */
export async function refreshPasskeys(view: HTMLElement): Promise<void> {
  const desc = view.querySelector<HTMLElement>('[data-passkeys-desc]')!;
  const control = view.querySelector<HTMLElement>('[data-passkey-control]')!;
  const list = view.querySelector<HTMLElement>('[data-passkey-list]')!;
  let st: PasskeysOverview;
  try { st = await api.passkeys(); } catch (e) { desc.textContent = (e as Error).message; return; }
  if (!view.isConnected) return;
  overview = st;
  const problem = st.unavailable || browserProblem();
  desc.innerHTML = problem
    ? `A device with a passkey signs in on its own, without a code from this computer. ${escapeHtml(problem)}`
    : `A device with a passkey signs in on its own, without a code from this computer: on the “not paired” page it
       chooses <b>Sign In with a Passkey</b>. Passkeys added here work at <span class="mono">https://${escapeHtml(st.rp_id || '')}</span>.`;
  control.innerHTML = problem ? '' : `<button class="btn" data-passkey-add>${icon('key')} Add a Passkey…</button>`;
  // Like other devices, passkeys are removed on the computer running Synapse, or after confirming
  // with a passkey on a signed-in device.
  const admin = st.this_computer || st.can_confirm;
  list.innerHTML = st.passkeys.map((p) => `<div class="backup-row" data-passkey="${escapeHtml(p.id)}">
      <div class="b-main"><div class="b-when">${escapeHtml(p.name)}</div>
        <div class="b-what">For ${escapeHtml(p.rp_id)} · added ${escapeHtml(formatDate(p.created, true))} · last used ${escapeHtml(relTime(p.last_used))}${p.last_addr && p.last_used ? ` from ${escapeHtml(p.last_addr)}` : ''}</div></div>
      ${admin ? '<button class="btn btn-sm" data-passkey-remove>Remove…</button>' : ''}</div>`).join('');
}

async function addPasskey(view: HTMLElement): Promise<void> {
  const name = await promptDialog('Name', {
    title: 'Add a passkey', value: 'Synapse', confirm: 'Continue',
    hint: 'Your browser or password manager saves the passkey under this name.',
  });
  if (name === null) return;
  try {
    const o = await asAdmin(() => api.passkeyOptions(name.trim()));   // the second passkey on: confirm first
    if (!o) return;
    const publicKey: PublicKeyCredentialCreationOptions = {
      rp: o.rp,
      user: { ...o.user, id: fromB64url(o.user.id) },
      challenge: fromB64url(o.challenge),
      pubKeyCredParams: o.pubKeyCredParams,
      authenticatorSelection: o.authenticatorSelection,
      attestation: o.attestation,
      excludeCredentials: (o.excludeCredentials || []).map((c: { type: 'public-key'; id: string }) => ({ ...c, id: fromB64url(c.id) })),
      timeout: o.timeout,
    };
    const cred = await navigator.credentials.create({ publicKey }) as PublicKeyCredential | null;
    if (!cred) return;
    const r = cred.response as AuthenticatorAttestationResponse;
    await api.addPasskey({ id: cred.id, clientDataJSON: b64url(r.clientDataJSON), attestationObject: b64url(r.attestationObject) });
    toast('Passkey added', { type: 'success' });
  } catch (e) {
    if ((e as Error).name === 'NotAllowedError') return;   // cancelled in the browser's dialog
    if ((e as Error).name === 'InvalidStateError') toastError(new Error('This device already has a passkey for Synapse.'));
    else toastError(e);
  }
  refreshPasskeys(view);
}

export function bindPasskeys(view: HTMLElement): void {
  view.querySelector('[data-passkey-control]')!.addEventListener('click', (e) => {
    if ((e.target as Element).closest('[data-passkey-add]')) addPasskey(view);
  });
  view.querySelector('[data-passkey-list]')!.addEventListener('click', async (e) => {
    const row = (e.target as Element).closest<HTMLElement>('[data-passkey]');
    if (!row || !(e.target as Element).closest('[data-passkey-remove]')) return;
    const pk = overview?.passkeys.find((p) => p.id === row.dataset.passkey);
    if (!pk) return;
    const ok = await confirmDialog(`Remove the passkey “${pk.name}”?`, {
      title: 'Remove passkey', confirm: 'Remove', danger: true,
      detail: 'It can no longer sign devices in. Devices already signed in with it stay signed in until you remove them above. '
        + 'Delete it from your password manager too.' });
    if (!ok) return;
    try { await asAdmin(() => api.removePasskey(pk.id)); } catch (err) { toastError(err); }
    refreshPasskeys(view);
  });
}
