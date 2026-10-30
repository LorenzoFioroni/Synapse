// Standalone: an unpaired device may fetch this module, but no app modules.
(() => {
  const code = new URLSearchParams(location.hash.slice(1)).get('code') || '';
  // The code is single-use and must not remain in the address bar or history.
  try { history.replaceState(null, '', location.pathname); } catch { /* ignore */ }
  if (!code) { document.getElementById('nocode')!.hidden = false; return; }
  document.getElementById('form')!.hidden = false;
  const input = document.getElementById('name') as HTMLInputElement;
  const button = document.getElementById('go') as HTMLButtonElement;
  const error = document.getElementById('error')!;
  const ua = navigator.userAgent;
  // iPadOS Safari presents itself as a Mac; touch points tell them apart.
  input.value = /iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1) ? 'iPad'
    : /iPhone/.test(ua) ? 'iPhone' : /Android/.test(ua) ? 'Android device' : '';
  button.onclick = async () => {
    button.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch('/api/devices/pair', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, name: input.value.trim() }),
      });
      const data = await response.json().catch(() => ({}));
      if (response.ok) { location.replace('/'); return; }
      error.textContent = data.error || `Pairing failed (${response.status}).`;
    } catch {
      error.textContent = 'Cannot reach Synapse. Is this device on the same network?';
    }
    error.hidden = false;
    button.disabled = false;
  };
})();
