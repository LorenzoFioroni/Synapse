// The companion's window. It talks only to the companion that served it (ui.py), with the key
// that the one-time ticket in the address was exchanged for; the key lives in this tab only.
'use strict';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let key = '';
try { key = sessionStorage.getItem('companionKey') || ''; } catch { /* storage off: the ticket below still works once */ }
let state = null;
let rendered = { settings: '', access: '', extension: '', connection: '' };
let changing = false;  // the connection form is open on a connected companion

async function call(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Companion-Key': key, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  return data;
}

function toast(text, error = false) {
  const el = $('toast');
  el.textContent = text;
  el.className = error ? 'toast err' : 'toast';
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, error ? 6000 : 2500);
}

async function act(button, path, body, done) {
  if (button) button.disabled = true;
  try {
    await call(path, body);
    if (done) toast(done);
    await refresh();
    return true;
  } catch (e) {
    toast(e.message, true);
    return false;
  } finally {
    if (button) button.disabled = false;
  }
}

function ago(seconds) {
  if (seconds < 90) return 'just now';
  if (seconds < 5400) return `${Math.round(seconds / 60)} minutes ago`;
  if (seconds < 129600) return `${Math.round(seconds / 3600)} hours ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

const STATES = {
  connected: ['ok', 'Connected'], connecting: ['warn', 'Connecting'], unreachable: ['err', 'Cannot reach the library'],
  refused: ['err', 'The token was refused'], stopped: ['warn', 'Not running'], none: ['', 'Not connected'],
};

function renderConnection(s) {
  const [cls, label] = STATES[s.connection.state] || STATES.none;
  $('connection').innerHTML = `<div class="status"><span class="dot ${cls}"></span><div><div>${esc(label)}${s.connected ? ` to <span class="mono">${esc(s.server)}</span>` : ''}</div>
    ${s.connection.message ? `<div class="hint">${esc(s.connection.message)}</div>` : ''}</div></div>`;
  const sig = `${s.server}|${s.name}`;
  if (rendered.connection !== sig && !$('connect').contains(document.activeElement)) {
    rendered.connection = sig;
    $('server').value = s.server;
    $('name').value = s.name;
    $('token').placeholder = s.connected ? 'Leave empty to keep the stored token' : 'syn_companion_…';
  }
  $('disconnect').hidden = !s.connected;
  $('cancel-change').hidden = !s.connected;
  $('connect').hidden = s.connected && !changing;
  $('change').hidden = !s.connected || changing;
  $('summary').textContent = s.connected ? `${s.name} · ${label.toLowerCase()}` : 'Connect to your Synapse library to begin';
}

function renderProviders(s) {
  const sig = JSON.stringify([s.settings, s.providers.map((p) => p.found)]);
  if (sig === rendered.settings) return;
  rendered.settings = sig;
  $('providers').innerHTML = s.providers.map((p) => `<div class="provider" data-provider="${esc(p.id)}">
      <label class="check"><input type="checkbox" data-enabled ${p.enabled ? 'checked' : ''}> <h3>${esc(p.label)}</h3>
        <span class="hint">${p.found ? '' : `· <span class="mono">${esc(p.id)}</span> not found on this computer`}</span></label>
      ${p.id === 'codex' && p.enabled ? '<div class="warn-text">Codex runs with its shell off, without your ~/.codex/config.toml and in a sandbox that reads only system files, but the companion cannot check which tools it has, as it does for Claude Code.</div>' : ''}
      ${p.models.length ? `<div class="chips"><span class="what">Models</span>${p.models.map((m) =>
        `<label><input type="checkbox" data-model="${esc(m.id)}" ${m.on ? 'checked' : ''} ${p.enabled ? '' : 'disabled'}> ${esc(m.label)}</label>`).join('')}</div>` : ''}
      ${p.efforts.length ? `<div class="chips"><span class="what">Effort</span>${p.efforts.map((e) =>
        `<label><input type="checkbox" data-effort="${esc(e.id)}" ${e.on ? 'checked' : ''} ${p.enabled ? '' : 'disabled'}> ${esc(e.id)}</label>`).join('')}</div>` : ''}
    </div>`).join('');
  const select = $('max-running');
  select.innerHTML = Array.from({ length: s.max_running }, (_, i) => `<option value="${i + 1}" ${s.settings.max_running === i + 1 ? 'selected' : ''}>${i + 1}</option>`).join('');
}

/** What the AI tools may do beyond Synapse's tools. Not redrawn while the list of sites is being edited. */
function renderAccess(s) {
  const a = s.settings.access;
  const sig = JSON.stringify(a);
  if (sig === rendered.access || document.activeElement?.id === 'sites') return;
  rendered.access = sig;
  const check = (key, label) => `<label class="check"><input type="checkbox" data-access="${key}" ${a[key] ? 'checked' : ''}> ${label}</label>`;
  $('access').innerHTML = `<h3>Beyond Synapse’s tools</h3>
    <div class="hint">Off unless you turn it on, for every question asked from your library.</div>
    ${check('web_search', 'Search the web')}
    <div class="hint indent">Claude Code: its web search. Codex: OpenAI’s search index, without live pages.</div>
    ${check('web_fetch', 'Open pages of these sites')}
    <div class="hint indent">Claude Code only. A site includes its subdomains, one per line.</div>
    <textarea id="sites" class="indent" rows="6" spellcheck="false" aria-label="Sites the AI tools may open" ${a.web_fetch ? '' : 'disabled'}>${esc(a.sites.join('\n'))}</textarea>
    <div class="row indent"><button class="btn" type="button" id="default-sites" ${a.web_fetch ? '' : 'disabled'}>Use the default list</button></div>
    ${a.web_search || a.web_fetch ? '<div class="warn-text">Text in a paper or on a page can steer the AI: it could put what it read into a search or an address of these sites.</div>' : ''}
    ${check('library', 'Change the library')}
    <div class="hint indent">Add papers, find their PDFs, tag and file them, highlight and move them to the Trash. Synapse must allow it too: Settings → AI Access → AI apps may change the library. Each change is listed under the answer.</div>`;
  $('default-sites').addEventListener('click', () => {
    $('sites').value = s.default_sites.join('\n');
    act(null, '/api/settings', { settings: readSettings() }, 'Saved');
    rendered.access = '';
  });
}

/** The settings as the controls show them. A model or effort list with everything ticked is
 *  stored as "all", so new ones a later version offers are included. */
function readSettings() {
  const settings = structuredClone(state.settings);
  for (const box of document.querySelectorAll('[data-provider]')) {
    const p = state.providers.find((x) => x.id === box.dataset.provider);
    const pick = (attr, all) => {
      const on = [...box.querySelectorAll(`[${attr}]`)].filter((i) => i.checked).map((i) => i.getAttribute(attr));
      return on.length === all.length ? null : on;
    };
    settings.providers[p.id] = {
      enabled: box.querySelector('[data-enabled]').checked,
      models: pick('data-model', p.models),
      efforts: pick('data-effort', p.efforts),
    };
  }
  settings.max_running = Number($('max-running').value);
  const on = (key) => document.querySelector(`[data-access="${key}"]`).checked;
  settings.access = {
    web_search: on('web_search'), web_fetch: on('web_fetch'), library: on('library'),
    sites: $('sites').value.split(/[\s,]+/).filter(Boolean),
  };
  return settings;
}

function renderExtension(s) {
  const e = s.extension;
  const sig = JSON.stringify(e);
  if (sig === rendered.extension) return;
  rendered.extension = sig;
  if (!e.installed) {
    $('extension').innerHTML = `<div class="hint">Save papers and PDFs to Synapse from Chrome, Chromium, Brave, Edge or Vivaldi. The extension asks this companion, which passes its requests to your library: the browser holds no token.</div>
      <div class="row"><button class="btn primary" data-ext="install">Install the extension</button></div>`;
    return;
  }
  const seen = e.seen;
  const [cls, line] = !e.browsers.length ? ['err', 'No browser knows the companion yet']
    : !seen ? ['warn', 'Not heard from the extension yet']
      : seen.current ? ['ok', `Working · last contact ${ago(seen.ago)}`]
        : ['warn', `Running an older version · last contact ${ago(seen.ago)}`];
  $('extension').innerHTML = `<div class="status"><span class="dot ${cls}"></span><div><div>${esc(line)}</div>
      <div class="hint">${e.browsers.length ? `Registered with ${esc(e.browsers.join(', '))}.` : 'Install Chrome, Chromium, Brave, Edge or Vivaldi, then click Repair.'}
        ${e.up_to_date === false ? 'An update is available.' : ''}</div></div></div>
    ${e.review ? `<div class="warn-text flush">An update changes what the extension may do: ${esc(e.review.join(', '))}. Apply it only if you trust your Synapse server.</div>` : ''}
    <div class="hint">Folder: <span class="mono">${esc(e.dir)}</span></div>
    ${!seen ? `<ol class="steps"><li>Open <span class="mono">chrome://extensions</span> and turn on Developer mode.</li>
      <li>Click <b>Load unpacked</b> and choose the folder above.</li><li>Open the extension’s Options to allow the library page.</li></ol>` : ''}
    <div class="row">${e.review ? '<button class="btn primary" data-ext="apply">Apply the update</button>' : ''}
      <button class="btn" data-ext="copy">Copy folder path</button><button class="btn" data-ext="repair">Repair</button></div>`;
}

const TRAY = {
  shown: () => 'The icon in the system tray opens this window.',
  missing: (t) => `For an icon in the system tray, install PySide6, then quit and start the companion again: <span class="mono">${esc(t.install)}</span>`,
  'no-tray': () => 'This desktop shows no tray icons (on GNOME, the AppIndicator extension adds them).',
  failed: () => 'The tray icon could not start; see Activity.',
};

function render(s) {
  state = s;
  renderConnection(s);
  $('card-ai').hidden = !s.connected;
  $('card-extension').hidden = !s.connected;
  if (s.connected) {
    renderProviders(s);
    renderAccess(s);
    renderExtension(s);
    $('running').textContent = s.running.length ? `Answering ${s.running.length} question${s.running.length > 1 ? 's' : ''} now.` : '';
  }
  $('autostart').checked = s.autostart;
  $('autostart-where').textContent = s.autostart ? `Entry: ${s.autostart_where}` : '';
  $('tray').innerHTML = TRAY[s.tray.status]?.(s.tray) ?? '';
  $('program').innerHTML = `Program: <span class="mono">${esc(s.program)}</span>${s.update ? ' · a newer version is on your server' : ''}`
    + (s.checked != null ? ` · checked for updates ${ago(s.checked)}` : '');
  $('check').hidden = !s.connected;
  $('update').hidden = !s.update;
  const log = $('log');
  const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
  log.textContent = s.log.join('\n');
  if (atEnd) log.scrollTop = log.scrollHeight;
}

async function refresh() {
  try {
    render(await call('/api/state?window=1'));
  } catch (e) {
    if (/key|ticket|403/i.test(e.message)) gone('This window has expired. Open Synapse Companion again.');
    else gone('Synapse Companion is not running. Start it again from your applications, or run it once.');
  }
}

let timer = 0;
function gone(text) {
  clearInterval(timer);
  $('main').innerHTML = `<div class="gone">${esc(text)}</div>`;
  $('summary').textContent = 'Stopped';
}

function bind() {
  $('connect').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const body = { server: $('server').value, token: $('token').value, name: $('name').value };
    act(ev.submitter, '/api/connect', body, 'Connected').then((ok) => {
      if (!ok) return;
      $('token').value = '';
      changing = false;
      rendered.connection = '';
      render(state);
    });
  });
  $('change').addEventListener('click', () => { changing = true; render(state); $('server').focus(); });
  $('cancel-change').addEventListener('click', () => { changing = false; rendered.connection = ''; render(state); });
  $('disconnect').addEventListener('click', (ev) => {
    if (confirm('Remove the token from this computer? Ask AI and the browser extension stop working until you connect again.')) {
      act(ev.currentTarget, '/api/disconnect', {}, 'Disconnected');
    }
  });
  $('card-ai').addEventListener('change', () => { act(null, '/api/settings', { settings: readSettings() }, 'Saved'); rendered.settings = ''; rendered.access = ''; });
  $('extension').addEventListener('click', (ev) => {
    const button = ev.target.closest('[data-ext]');
    if (!button) return;
    const what = button.dataset.ext;
    if (what === 'copy') {
      navigator.clipboard.writeText(state.extension.dir).then(() => toast('Folder path copied'), () => toast('Could not copy', true));
      return;
    }
    act(button, '/api/extension', { action: what }, what === 'apply' ? 'Updated' : 'Done');
  });
  $('autostart').addEventListener('change', (ev) => act(null, '/api/autostart', { on: ev.target.checked }));
  $('check').addEventListener('click', async (ev) => {
    const button = ev.currentTarget;
    button.disabled = true;
    try {
      toast((await call('/api/check', {})).message);
      await refresh();
    } catch (e) {
      toast(e.message, true);
    } finally {
      button.disabled = false;
    }
  });
  $('update').addEventListener('click', (ev) => act(ev.currentTarget, '/api/update', {}, 'Updating…'));
  $('quit').addEventListener('click', async () => {
    if (!confirm('Quit Synapse Companion? Ask AI and the browser extension stop working until it starts again.')) return;
    try { await call('/api/quit', {}); } catch { /* it is going away */ }
    gone('Synapse Companion has stopped.');
  });
}

async function start() {
  const ticket = new URLSearchParams(location.search).get('ticket');
  if (ticket) {
    history.replaceState(null, '', '/');
    try {
      key = (await call('/api/session', { ticket })).key;
      try { sessionStorage.setItem('companionKey', key); } catch { /* this tab only, then */ }
    } catch { /* the stored key may still work */ }
  }
  bind();
  await refresh();
  timer = setInterval(refresh, 2000);
}

// Closing the window tells the companion at once (it takes the Dock icon away).
window.addEventListener('pagehide', () => {
  fetch('/api/window', { method: 'POST', keepalive: true, headers: { 'X-Companion-Key': key, 'Content-Type': 'application/json' }, body: '{"open":false}' }).catch(() => {});
});

start();
