import { companionHello } from './connection.js';

const status = /** @type {HTMLElement} */ (document.getElementById('status'));
const bridgeInput = /** @type {HTMLInputElement} */ (document.getElementById('find-pdfs'));
const receiverInput = /** @type {HTMLInputElement} */ (document.getElementById('pdf-receiver'));

/** The origin permission the library page bridge needs. @param {string} server */
const originPattern = (server) => { const url = new URL(server); return `${url.protocol}//${url.hostname}/*`; };

/** @type {string} */
let server = '';

/** @param {string} text */
const show = (text) => { status.textContent = text; };

async function load() {
  try {
    const hello = await companionHello();
    server = hello.server;
    show(`✓ Connected to ${server}${hello.name ? ` as ${hello.name}` : ''}`);
  } catch (error) {
    show(`${error instanceof Error ? error.message : 'Synapse Companion did not answer.'} Install or open Synapse Companion on this computer and connect it to your library; it registers itself with Chrome, Chromium, Brave, Edge and Vivaldi.`);
    return;
  }
  const { pdfReceiver } = /** @type {{pdfReceiver?: {server: string, enabled: boolean}}} */ (await chrome.storage.local.get('pdfReceiver'));
  receiverInput.checked = pdfReceiver?.enabled === true && pdfReceiver.server === server;
  bridgeInput.checked = await chrome.permissions.contains({ origins: [originPattern(server)] });
  bridgeInput.disabled = false;
  receiverInput.disabled = false;
}

bridgeInput.onchange = () => {
  const origins = [originPattern(server)];
  // Request before any await so Chrome sees the click's user gesture.
  const done = bridgeInput.checked ? chrome.permissions.request({ origins }) : chrome.permissions.remove({ origins });
  done.then(async (granted) => {
    if (!granted) show('Permission was declined.');
    bridgeInput.checked = await chrome.permissions.contains({ origins });
  }).catch((error) => show(String(error.message)));
};

receiverInput.onchange = () => {
  const enabled = receiverInput.checked;
  const request = enabled ? chrome.permissions.request({ origins: ['https://*/*'] }) : Promise.resolve(true);
  request.then(async (granted) => {
    if (!granted) { receiverInput.checked = false; show('Permission was declined. Allow access to publisher pages to handle requests.'); return; }
    await chrome.storage.local.set({ pdfReceiver: { server, enabled } });
  }).catch((error) => show(String(error.message)));
};

void load();
