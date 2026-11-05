// Shared connection settings keep credentials tied to the server that received them.
export const DEFAULT_SERVER = 'http://127.0.0.1:8000';

/** @param {string} value */
export function serverAddress(value) {
  let address = value.trim() || DEFAULT_SERVER;
  if (!/^https?:\/\//i.test(address)) address = `${/^(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/i.test(address) ? 'http' : 'https'}://${address}`;
  const url = new URL(address);
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('Use the server address only, without a path, password, query or fragment.');
  }
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local)) {
    throw new Error('Remote servers require an HTTPS address.');
  }
  return { server: url.origin, local, permission: `${url.protocol}//${url.hostname}/*` };
}

export async function credentialStorage() {
  // Only trusted extension pages may read credentials; page-injected scripts cannot.
  await chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });
  return chrome.storage.local;
}

export async function loadConnection() {
  const stored = /** @type {{ server?: string }} */ (await chrome.storage.sync.get('server'));
  const connection = serverAddress(stored.server || DEFAULT_SERVER);
  const saved = /** @type {{ connection?: { server: string, token: string, scope?: string } }} */ (await (await credentialStorage()).get('connection'));
  // Earlier builds stored general AI tokens. Require a dedicated browser token instead.
  if (saved.connection && saved.connection.scope !== 'connector') {
    await chrome.storage.local.remove('connection');
    return { ...connection, token: '' };
  }
  return { ...connection, token: saved.connection?.server === connection.server ? saved.connection.token : '' };
}

/** @param {string} server @param {string} token @param {string} path @param {RequestInit} [opts] */
export function libraryFetch(server, token, path, opts = {}) {
  const headers = new Headers(opts.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  // Do not send the token to a redirect target or depend on browser sign-in cookies.
  return fetch(server + path, { ...opts, headers, credentials: 'omit', redirect: 'error' });
}
