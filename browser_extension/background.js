import { loadConnection, libraryFetch } from './connection.js';
import { libraryPdfRequest } from './bridge.js';

async function syncBridge() {
  const { server } = await loadConnection();
  const url = new URL(server);
  const matches = [`${url.protocol}//${url.hostname}/*`];
  await chrome.scripting.unregisterContentScripts({ ids: ['synapse-library'] }).catch(() => {});
  if (await chrome.permissions.contains({ origins: matches })) {
    await chrome.scripting.registerContentScripts([{
      id: 'synapse-library', matches, js: ['library.js'], runAt: 'document_start',
    }]);
  }
}

let syncing = Promise.resolve();
const refreshBridge = () => { syncing = syncing.then(syncBridge).catch(() => {}); };
chrome.runtime.onInstalled.addListener(refreshBridge);
chrome.runtime.onStartup.addListener(refreshBridge);
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'sync' && changes.server) refreshBridge();
});
chrome.permissions.onAdded.addListener(refreshBridge);
chrome.permissions.onRemoved.addListener(refreshBridge);
refreshBridge();

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message?.type !== 'find-publisher-pdfs') return;
  void (async () => {
    const { server, token } = await loadConnection();
    const ids = libraryPdfRequest(message, sender, server);
    if (!ids) { reply({ accepted: false }); return; }
    // An installed extension may have an expired/revoked credential or an older server. Only
    // take over the app's fallback after checking that this exact library can accept scoped work.
    const ping = await libraryFetch(server, token, '/api/connector/ping', { signal: AbortSignal.timeout(3000) });
    const data = await ping.json();
    if (!ping.ok || data.app !== 'synapse' || !data.capabilities?.publisher_pdfs) { reply({ accepted: false }); return; }
    const query = new URLSearchParams({ ids: ids.join(','), auto: '1', fallback: '1' });
    await chrome.windows.create({
      url: chrome.runtime.getURL(`find_pdfs.html?${query}`), type: 'popup', width: 650, height: 760,
    });
    reply({ accepted: true });
  })().catch(() => reply({ accepted: false }));
  return true;
});
