import { loadConnection, serverAddress, libraryFetch, credentialStorage } from './connection.js';

const input = /** @type {HTMLInputElement} */ (document.getElementById('server'));
const tokenInput = /** @type {HTMLInputElement} */ (document.getElementById('token'));
const saveButton = /** @type {HTMLButtonElement} */ (document.getElementById('save'));
const status = /** @type {HTMLElement} */ (document.getElementById('status'));

loadConnection().then((connection) => {
  input.value = connection.server;
  tokenInput.value = connection.token;
}).catch((error) => { status.textContent = String(error.message); }).finally(() => {
  input.disabled = false;
  tokenInput.disabled = false;
  saveButton.disabled = false;
});

// A token entered for one server must not silently follow a changed address.
input.addEventListener('input', () => { tokenInput.value = ''; });

saveButton.onclick = async () => {
  try {
    const { server, local, permission } = serverAddress(input.value);
    const token = tokenInput.value.trim();
    if ((!local && !token) || (token && !token.startsWith('syn_connector_'))) {
      throw new Error('Paste a browser token from Synapse’s Settings → AI Access → New Browser Token.');
    }
    // Call request before any await so Chrome sees the Save button's user gesture.
    if (server.startsWith('https:') && !await chrome.permissions.request({ origins: [permission] })) {
      throw new Error('Connection permission was declined. Allow access to this server to save.');
    }
    await (await credentialStorage()).set({ connection: { server, token, scope: 'connector' } });
    await chrome.storage.sync.set({ server });
    input.value = server;
    status.textContent = 'Saved — checking…';
    const response = await libraryFetch(server, token, '/api/connector/ping');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Server returned ${response.status}.`);
    status.textContent = ['synapse', 'zotero-clone'].includes(data.app)
      ? `✓ Connected (${data.counts.library} items)` : 'Saved, but that address is not the library.';
  } catch (error) {
    status.textContent = error instanceof Error ? error.message : 'Could not connect to the library.';
  }
};
