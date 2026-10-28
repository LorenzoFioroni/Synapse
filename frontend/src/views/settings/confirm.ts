// Confirming with a passkey before an administrative action (pairing a device, making an access token,
// adding or removing passkeys, removing other devices) on a device that is not the computer running
// Synapse. The server answers such a request with 403 and ``confirm: true``; the app then asks the
// browser for the passkey and tries once more. See backend/access/confirm.py.
import { api, ApiError } from '../../api.js';

export function b64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function fromB64url(text: string): Uint8Array<ArrayBuffer> {
  let s = text.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Whether a failed request asked for a passkey confirmation. */
export function needsConfirmation(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403 && !!(e.data as { confirm?: boolean } | null)?.confirm;
}

/** Ask the browser for one of Synapse's passkeys and tell the server. False when cancelled. */
export async function confirmWithPasskey(): Promise<boolean> {
  const o = await api.confirmOptions();
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
  await api.confirm({
    id: cred.id, clientDataJSON: b64url(r.clientDataJSON), authenticatorData: b64url(r.authenticatorData),
    signature: b64url(r.signature), userHandle: r.userHandle ? b64url(r.userHandle) : null,
  });
  return true;
}

/** Run ``action``; if the server wants a passkey confirmation first, get it and run it again.
 * Resolves to null when the person cancelled the passkey prompt. */
export async function asAdmin<T>(action: () => Promise<T>): Promise<T | null> {
  try {
    return await action();
  } catch (e) {
    if (!needsConfirmation(e)) throw e;
    if (!await confirmWithPasskey()) return null;
    return await action();
  }
}
