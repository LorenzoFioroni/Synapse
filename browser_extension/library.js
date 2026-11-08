// Isolated content script: the credential store is restricted to trusted extension contexts.
// The worker independently checks the sender's exact configured origin (including its port).
const markReady = () => document.documentElement?.setAttribute('data-synapse-extension', 'pdfs');
markReady();
if (!document.documentElement) document.addEventListener('DOMContentLoaded', markReady, { once: true });
document.addEventListener('synapse:publisher-pdfs', (event) => {
  if (!(event instanceof CustomEvent) || typeof event.detail !== 'string') return;
  let request;
  try { request = JSON.parse(event.detail); } catch { return; }
  if (!request || typeof request !== 'object') return;
  if (typeof request.requestId !== 'string' || !/^[A-Za-z0-9-]{1,128}$/.test(request.requestId)) return;
  chrome.runtime.sendMessage({ type: 'find-publisher-pdfs', ids: request.ids }).then((result) => {
    document.dispatchEvent(new CustomEvent('synapse:publisher-pdfs-result', {
      detail: JSON.stringify({ requestId: request.requestId, accepted: result?.accepted === true }),
    }));
  }).catch(() => {
    document.dispatchEvent(new CustomEvent('synapse:publisher-pdfs-result', {
      detail: JSON.stringify({ requestId: request.requestId, accepted: false }),
    }));
  });
});
