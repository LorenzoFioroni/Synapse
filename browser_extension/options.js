const DEFAULT_SERVER = 'http://127.0.0.1:8000';
const input = document.getElementById('server');
const status = document.getElementById('status');

chrome.storage.sync.get('server').then(({ server }) => { input.value = server || DEFAULT_SERVER; });

document.getElementById('save').onclick = async () => {
  let v = input.value.trim().replace(/\/+$/, '') || DEFAULT_SERVER;
  if (!/^https?:\/\//.test(v)) v = `http://${v}`;
  input.value = v;
  await chrome.storage.sync.set({ server: v });
  status.textContent = 'Saved — checking…';
  try {
    const r = await fetch(`${v}/api/connector/ping`);
    const d = await r.json();
    status.innerHTML = ['synapse', 'zotero-clone'].includes(d.app) ? `<span style="color:var(--ok)">✓ Connected (${d.counts.library} items)</span>` : 'Saved, but that address is not the library.';
  } catch {
    status.innerHTML = '<span style="color:var(--err)">Saved, but the library is not reachable there right now.</span>';
  }
};
