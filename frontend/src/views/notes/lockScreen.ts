// Locked notes (backend/notes/locks.py): the lock shown in a locked note's tab, unlocking with the
// password from Settings → Notes or a passkey, and locking or removing a lock from the notes list.
// The server opens a note for this page only; it locks again after Settings' timeout without use,
// when its tab closes or when the page goes away.
import { api, ApiError, CLIENT_ID } from '../../api.js';
import { icon } from '../../icons.js';
import type { NoteLocks } from '../../types.js';
import { openDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml, h } from '../../util.js';
import { asAdmin, b64url, fromB64url } from '../settings/confirm.js';

/** Whether a request failed because the note is locked for this page. */
export const isLockedError = (e: unknown): boolean => e instanceof ApiError && e.status === 423;

let status: Promise<NoteLocks> | null = null;
/** What this page can unlock with (asked once; ``fresh`` after the password changed). */
export function lockStatus(fresh = false): Promise<NoteLocks> {
  if (!status || fresh) status = api.noteLocks().catch((e) => { status = null; throw e; });
  return status;
}

/** Ask the browser for one of Synapse's passkeys and open the note with it. False when cancelled. */
async function unlockWithPasskey(path: string): Promise<boolean> {
  const o = await api.unlockOptions();
  let cred: PublicKeyCredential | null;
  try {
    cred = await navigator.credentials.get({ publicKey: {
      challenge: fromB64url(o.challenge), rpId: o.rpId, allowCredentials: [],
      userVerification: o.userVerification, timeout: o.timeout } }) as PublicKeyCredential | null;
  } catch (e) {
    if ((e as Error).name === 'NotAllowedError') return false;   // cancelled, or no passkey here
    throw e;
  }
  if (!cred) return false;
  const r = cred.response as AuthenticatorAssertionResponse;
  await api.unlockNote(path, { credential: {
    id: cred.id, clientDataJSON: b64url(r.clientDataJSON), authenticatorData: b64url(r.authenticatorData),
    signature: b64url(r.signature), userHandle: r.userHandle ? b64url(r.userHandle) : null,
  } });
  return true;
}

/**
 * The lock for ``path`` in ``host``: a big lock, the note's title, a password field and, where
 * passkeys work, a passkey button. ``onUnlocked`` runs once the server opened the note.
 */
export function renderLockScreen(host: HTMLElement, path: string, title: string, onUnlocked: () => void): void {
  const el = h(`<div class="note-locked empty-state" role="region" aria-label="Locked note">
    <div class="note-locked-icon">${icon('lock')}</div>
    <h3>${escapeHtml(title)}</h3>
    <p>This note is locked.</p>
    <form class="note-locked-form" autocomplete="off">
      <input class="input" type="password" name="password" placeholder="Password" aria-label="Password" autocomplete="current-password">
      <button class="btn btn-primary" type="submit">Unlock</button>
    </form>
    <button class="btn note-locked-passkey" type="button" hidden>${icon('key', 'icon-sm')} Use a passkey</button>
    <p class="note-locked-hint" hidden></p>
  </div>`);
  host.replaceChildren(el);
  const form = el.querySelector<HTMLFormElement>('form')!;
  const input = form.querySelector<HTMLInputElement>('input')!;
  const passkey = el.querySelector<HTMLButtonElement>('.note-locked-passkey')!;
  const hint = el.querySelector<HTMLElement>('.note-locked-hint')!;
  let busy = false;
  const run = async (fn: () => Promise<boolean>) => {
    if (busy) return;
    busy = true;
    el.classList.add('busy');
    try { if (await fn()) onUnlocked(); } catch (e) {
      if (e instanceof ApiError && e.status === 403) { input.select(); el.classList.remove('shake'); void el.offsetWidth; el.classList.add('shake'); toast('Wrong password.', { type: 'error' }); } else toastError(e);
    } finally { busy = false; el.classList.remove('busy'); }
  };
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!input.value) { input.focus(); return; }
    run(async () => { await api.unlockNote(path, { password: input.value }); input.value = ''; return true; });
  });
  passkey.addEventListener('click', () => run(() => unlockWithPasskey(path)));
  lockStatus().then((s) => {
    passkey.hidden = !s.passkeys;
    if (!s.password_set) { form.hidden = true; hint.hidden = false; hint.textContent = s.passkeys ? '' : 'Set a password for locked notes in Settings → Notes.'; }
  }).catch(() => null);
  requestAnimationFrame(() => { if (el.isConnected && !form.hidden) input.focus({ preventScroll: true }); });
}

/** Unlock ``path`` in a dialog (removing a lock from the notes list). False when cancelled. */
function unlockDialog(path: string, title: string): Promise<boolean> {
  return openDialog({
    title: 'Unlock note',
    render: (body, close) => renderLockScreen(body, path, title, () => close(true)),
  }).then((v) => v === true);
}

/** Ask for a new password (and the current one when there is one). Resolves false when cancelled. */
export async function setPasswordDialog(): Promise<boolean> {
  const s = await lockStatus(true);
  const ok = await openDialog({
    title: s.password_set ? 'Change the password for locked notes' : 'Set a password for locked notes',
    render: (body) => {
      body.innerHTML = `<div class="lock-password-form">
        ${s.password_set ? '<label>Current password<input class="input" type="password" data-f="current" autocomplete="current-password"></label>' : ''}
        <label>New password<input class="input" type="password" data-f="password" autocomplete="new-password"></label>
        <label>Repeat it<input class="input" type="password" data-f="again" autocomplete="new-password"></label>
        <p class="muted">It opens every locked note. It is kept on the server as a hash, never in Settings or backups; a passkey opens locked notes too.</p>
      </div>`;
      requestAnimationFrame(() => body.querySelector<HTMLInputElement>('input')?.focus());
    },
    buttons: [{ label: 'Cancel', value: null }, {
      label: 'Save', kind: 'primary', value: true, onClick: async (_close, button) => {
        const form = button.closest('.dialog')!;
        const f = (n: string) => form.querySelector<HTMLInputElement>(`[data-f="${n}"]`)?.value ?? '';
        if (f('password') !== f('again')) { toast('The two passwords differ.', { type: 'error' }); return false; }
        try {
          return !!await asAdmin(() => api.setNoteLockPassword(f('password'), f('current') || undefined));
        } catch (e) { toastError(e); return false; }
      },
    }],
  });
  if (ok === true) { await lockStatus(true); toast('Password saved.', { type: 'success' }); return true; }
  return false;
}

/** Lock a note, or remove its lock (unlocking it first when this page has not). */
export async function setLocked(path: string, title: string, locked: boolean): Promise<boolean> {
  try {
    if (locked && !(await lockStatus()).password_set && !await setPasswordDialog()) return false;
    try {
      await api.lockNote(path, locked);
    } catch (e) {
      if (locked || !isLockedError(e)) throw e;
      if (!await unlockDialog(path, title)) return false;
      await api.lockNote(path, false);
    }
    toast(locked ? `“${title}” is locked.` : `“${title}” is no longer locked.`, { type: 'success' });
    return true;
  } catch (e) { toastError(e); return false; }
}

/** The page goes away: lock again what it unlocked (the server's timeout does it otherwise). */
export function relockOnUnload(paths: string[]): void {
  if (!paths.length) return;
  try {
    fetch('/api/notes/relock', {
      method: 'POST', keepalive: true,
      headers: { 'Content-Type': 'application/json', 'X-Synapse-Client': CLIENT_ID },
      body: JSON.stringify({ paths }),
    });
  } catch { /* they lock after the timeout */ }
}
