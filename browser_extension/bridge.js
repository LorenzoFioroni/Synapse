// Messages from a library page never carry credentials or publisher URLs. Only the configured
// library's top frame may ask for its own item IDs; the finder resolves them through the connector.
/** @param {unknown} message @param {{url?: string, frameId?: number}} sender @param {string} server */
export function libraryPdfRequest(message, sender, server) {
  if (!message || typeof message !== 'object' || !('type' in message) || message.type !== 'find-publisher-pdfs') return null;
  try {
    if (sender.frameId !== 0 || new URL(sender.url || '').origin !== server) return null;
  } catch { return null; }
  const ids = 'ids' in message ? message.ids : null;
  if (!Array.isArray(ids) || !ids.length || ids.length > 200
    || !ids.every((id) => typeof id === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(id))) return null;
  return [...new Set(/** @type {string[]} */ (ids))];
}
