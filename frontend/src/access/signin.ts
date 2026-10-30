// Standalone: the sign-in page is served even when app modules are inaccessible.
(() => {
  function enc(buffer: ArrayBuffer): string {
    let text = '';
    for (const byte of new Uint8Array(buffer)) text += String.fromCharCode(byte);
    return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function dec(text: string): Uint8Array<ArrayBuffer> {
    const padded = text.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(text.length / 4) * 4, '=');
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
  }
  async function post(url: string, data: object = {}) {
    const response = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `Signing in failed (${response.status}).`);
    return result;
  }
  const host = location.hostname;
  const byIp = /^[\d.]+$/.test(host) || host.includes(':') || host.startsWith('[');
  if (!window.PublicKeyCredential || !window.isSecureContext || byIp) {
    document.getElementById('nopasskey')!.hidden = false;
    return;
  }
  document.getElementById('passkey')!.hidden = false;
  const ua = navigator.userAgent;
  // iPadOS Safari presents itself as a Mac; touch points tell them apart.
  const name = /iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1) ? 'iPad'
    : /iPhone/.test(ua) ? 'iPhone' : /Android/.test(ua) ? 'Android device' : '';
  const button = document.getElementById('go') as HTMLButtonElement;
  const error = document.getElementById('error')!;
  button.onclick = async () => {
    button.disabled = true;
    error.hidden = true;
    try {
      const options = await post('/api/passkeys/login/options');
      const credential = await navigator.credentials.get({ publicKey: {
        challenge: dec(options.challenge), rpId: options.rpId, allowCredentials: [],
        userVerification: options.userVerification, timeout: options.timeout,
      } }) as PublicKeyCredential | null;
      if (!credential) throw new Error('Signing in was cancelled.');
      const response = credential.response as AuthenticatorAssertionResponse;
      await post('/api/passkeys/login', { name, credential: {
        id: credential.id, clientDataJSON: enc(response.clientDataJSON), authenticatorData: enc(response.authenticatorData),
        signature: enc(response.signature), userHandle: response.userHandle ? enc(response.userHandle) : null,
      } });
      location.reload();
    } catch (e) {
      error.textContent = e instanceof Error && e.name === 'NotAllowedError'
        ? 'Signing in was cancelled, or this device has no passkey for Synapse.'
        : (e instanceof Error && e.message) || 'Signing in failed.';
      error.hidden = false;
      button.disabled = false;
    }
  };
})();
