import { companionHello, libraryFetch } from './connection.js';
import { libraryPdfRequest } from './bridge.js';
import { refreshPdfReceiver } from './receiver.js';
import { recordBuild, reloadIfUpdated } from './selfUpdate.js';

async function syncBridge() {
  const url = new URL((await companionHello()).server);
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
chrome.runtime.onInstalled.addListener(() => {
  void recordBuild().catch(() => {});
  // Earlier versions kept a token and a server address here; the companion holds them now.
  void chrome.storage.local.remove('connection');
  void chrome.storage.sync.remove('server');
});
chrome.runtime.onStartup.addListener(refreshBridge);
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.pdfReceiver) refreshPdfReceiver();
});
chrome.permissions.onAdded.addListener(() => { refreshBridge(); refreshPdfReceiver(); });
chrome.permissions.onRemoved.addListener(() => { refreshBridge(); refreshPdfReceiver(); });
refreshBridge();   // also tells the companion that the extension is running
refreshPdfReceiver();
// a moment later, so a load's onInstalled has recorded the build it started from first
setTimeout(() => { void reloadIfUpdated().catch(() => {}); }, 2000);

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message?.type !== 'find-publisher-pdfs') return;
  void (async () => {
    const { server } = await companionHello();
    const ids = libraryPdfRequest(message, sender, server);
    if (!ids) { reply({ accepted: false }); return; }
    // An older library may not support scoped work. Only take over the app's fallback after
    // checking that this exact library can accept it.
    const ping = await libraryFetch('/api/connector/ping', { signal: AbortSignal.timeout(3000) });
    const data = await ping.json();
    if (!ping.ok || data.app !== 'synapse' || !data.capabilities?.publisher_pdfs) { reply({ accepted: false }); return; }
    const query = new URLSearchParams({ ids: ids.join(','), auto: '1', fallback: '1', library: server });
    await chrome.windows.create({
      url: chrome.runtime.getURL(`find_pdfs.html?${query}`), type: 'popup', width: 650, height: 760,
    });
    reply({ accepted: true });
  })().catch(() => reply({ accepted: false }));
  return true;
});
