// Settings → AI Access: separate tokens for MCP clients, browser saves and the companion, with shared
// administrative confirmation and revocation. See backend/access/tokens.py.
import { api } from '../../api.js';
import { refreshAiStatus } from '../../ai/status.js';
import { asAdmin } from './confirm.js';
import { icon } from '../../icons.js';
import { confirmDialog, promptDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import type { AccessTokensOverview } from '../../types.js';
import { escapeHtml, formatDate } from '../../util.js';
import { relTime } from './devices.js';

let overview: AccessTokensOverview | null = null;
/** A token just made: shown until Settings closes, because the server does not keep it. */
let fresh: { token: string; name: string; index: number; app: App; scope: 'mcp' | 'connector' | 'companion' } | null = null;

type App = 'claude' | 'codex' | 'other';
const APPS: [App, string][] = [['claude', 'Claude Code'], ['codex', 'Codex'], ['other', 'Other app']];

/** What to run (or enter) on the other computer so ``app`` uses Synapse with this token. */
function command(app: App, url: string, token: string): string {
  if (app === 'codex') {
    // Codex reads the token from an environment variable, which the shell must set for it
    return `export SYNAPSE_TOKEN=${token}\ncodex mcp add synapse --url ${url} --bearer-token-env-var SYNAPSE_TOKEN`;
  }
  if (app === 'other') return `URL: ${url}\nHeader: Authorization: Bearer ${token}`;
  return `claude mcp add --scope user --transport http synapse ${url} --header "Authorization: Bearer ${token}"`;
}

const HINTS: Record<App, string> = {
  claude: 'On the other computer, run:',
  codex: 'On the other computer, run this (and put the first line in your shell profile, so Codex always finds the token):',
  other: 'In the other app, add a Streamable HTTP MCP server with:',
};

/** What to run on the computer that has the AI app, once the script is downloaded. The token is
 *  pasted when ``connect`` asks, so it is in no command line (shell history, process list). */
function companionCommand(server: string): string {
  return `python3 synapse_companion.py connect ${server}
python3 synapse_companion.py run`;
}

function renderFresh(box: HTMLElement): void {
  if (!fresh || !overview) { box.innerHTML = ''; return; }
  if (fresh.scope === 'connector') {
    box.innerHTML = `<div class="s-title">Browser token for ${escapeHtml(fresh.name)}</div>
      <div class="s-desc">Allows saving and filing items only, until you remove it here.
      Copy it now and paste it into the browser extension’s Options for one-time setup.</div>
      <pre class="mono ai-command">${escapeHtml(fresh.token)}</pre>
      <button class="btn btn-sm" data-ai-copy-token>${icon('copy')} Copy Token</button>
      <button class="btn btn-sm" data-ai-done>Done</button>`;
    return;
  }
  const eps = overview.endpoints;
  const ep = eps[fresh.index] || eps[0];
  if (fresh.scope === 'companion') {
    const server = ep ? ep.url.replace(/\/mcp\/?$/, '') : 'https://synapse.example.com';
    box.innerHTML = `<div class="s-title">Companion token for ${escapeHtml(fresh.name)}</div>
      <div class="s-desc">Copy the token now: Synapse keeps only a fingerprint and cannot show it again. On the computer that has your AI app
      (it needs Python 3), download the script, then run these commands and paste the token when asked. “Ask AI” then appears in notes and in the PDF viewer,
      and the companion keeps the browser extension up to date.</div>
      <a class="btn btn-sm" href="/api/ai/companion-script" download="synapse_companion.py" data-ai-download>${icon('download')} Download synapse_companion.py</a>
      <pre class="mono ai-command" data-ai-command>${escapeHtml(companionCommand(server))}</pre>
      ${eps.length > 1 ? `<div class="pair-addrs">Address: ${eps.map((l, i) => `<button class="token${i === fresh!.index ? ' active' : ''}" data-ai-addr="${i}">${escapeHtml(l.label)}</button>`).join(' ')}</div>` : ''}
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-sm" data-ai-copy-cmd>${icon('copy')} Copy Commands</button>
        <button class="btn btn-sm" data-ai-copy-token>${icon('copy')} Copy Token</button>
        <button class="btn btn-sm" data-ai-done>Done</button></div>`;
    return;
  }
  const choices = eps.length > 1 ? `<div class="pair-addrs">Address: ${eps.map((l, i) =>
    `<button class="token${i === fresh!.index ? ' active' : ''}" data-ai-addr="${i}">${escapeHtml(l.label)}</button>`).join(' ')}</div>` : '';
  box.innerHTML = `<div class="s-title">Token for ${escapeHtml(fresh.name)}</div>
    <div class="s-desc">Copy it now: Synapse keeps only a fingerprint and cannot show it again.</div>
    <div class="segmented ai-apps" role="group" aria-label="App">${APPS.map(([a, label]) =>
      `<button data-ai-app="${a}" class="${a === fresh!.app ? 'active' : ''}">${label}</button>`).join('')}</div>
    <div class="s-desc">${HINTS[fresh.app]}</div>
    <pre class="mono ai-command" data-ai-command>${escapeHtml(command(fresh.app, ep ? ep.url : '<url>/mcp', fresh.token))}</pre>
    ${choices}
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-sm" data-ai-copy-cmd>${icon('copy')} Copy ${fresh.app === 'other' ? 'Both' : 'Command'}</button>
      <button class="btn btn-sm" data-ai-copy-token>${icon('copy')} Copy Token Only</button>
      <button class="btn btn-sm" data-ai-done>Done</button></div>`;
}

/** Show the access tokens in the open Settings page ``view``. */
export async function refreshAiAccess(view: HTMLElement): Promise<void> {
  const desc = view.querySelector<HTMLElement>('[data-ai-desc]')!;
  const control = view.querySelector<HTMLElement>('[data-ai-control]')!;
  const list = view.querySelector<HTMLElement>('[data-ai-list]')!;
  let st: AccessTokensOverview;
  try { st = await api.accessTokens(); } catch (e) { desc.textContent = (e as Error).message; return; }
  if (!view.isConnected) return;
  overview = st;
  const where = st.endpoints.length ? ` The address is <span class="mono">${escapeHtml(st.endpoints[0].url)}</span>.` : '';
  desc.innerHTML = (st.lan ? `Each app gets its own token.${where}`
    : `Synapse only answers this computer. For another computer on the same network, start it with <span class="mono">python server.py --lan</span>.
      For access through an HTTPS tunnel, use <span class="mono">python server.py --public-url https://synapse.example.com</span> instead.
      Do not combine <span class="mono">--lan</span> with <span class="mono">--public-url</span>.${where}`)
    + (st.this_computer ? '' : st.can_confirm ? ' You confirm with your passkey before making or removing a token.'
      : ' Tokens are made on the computer running Synapse, or on a device signed in with a passkey.');
  const admin = st.this_computer || st.can_confirm;
  control.innerHTML = admin ? `<button class="btn" data-ai-new>${icon('plus')} New Token…</button> <button class="btn" data-connector-new>${icon('plus')} New Browser Token…</button> <button class="btn" data-companion-new>${icon('plus')} New Companion Token…</button>` : '';
  renderFresh(view.querySelector<HTMLElement>('[data-ai-fresh]')!);
  list.innerHTML = st.tokens.length ? st.tokens.map((t) => `<div class="backup-row" data-ai-token="${escapeHtml(t.id)}">
      <div class="b-main"><div class="b-when">${escapeHtml(t.name)}</div>
        <div class="b-what">${t.scope === 'connector' ? 'Browser extension · save and file only' : t.scope === 'companion' ? 'Companion · local AI and extension updates' : 'AI app · MCP access'}</div>
        <div class="b-what">Made ${escapeHtml(formatDate(t.created, true))} · last used ${escapeHtml(relTime(t.last_used))}${t.last_addr ? ` from ${escapeHtml(t.last_addr)}` : ''}</div></div>
      ${admin ? '<button class="btn btn-sm" data-ai-remove>Remove…</button>' : ''}</div>`).join('')
    : '<div class="s-desc">No tokens yet.</div>';
}

/** Forget a token shown on screen (Settings is closing). */
export function forgetFreshToken(): void {
  fresh = null;
}

async function copy(text: string, what: string): Promise<void> {
  try { await navigator.clipboard.writeText(text); toast(`${what} copied`, { type: 'success' }); } catch (err) { toastError(err); }
}

export function bindAiAccess(view: HTMLElement): void {
  view.querySelector('[data-ai-control]')!.addEventListener('click', async (e) => {
    const target = e.target as Element;
    if (!target.closest('[data-ai-new], [data-connector-new], [data-companion-new]')) return;
    const scope = target.closest('[data-connector-new]') ? 'connector' : target.closest('[data-companion-new]') ? 'companion' : 'mcp';
    const name = await promptDialog('Name', {
      title: { connector: 'New browser token', companion: 'New companion token', mcp: 'New access token' }[scope],
      placeholder: { connector: 'Browser extension', companion: 'Work laptop', mcp: 'Work laptop — Codex' }[scope], confirm: 'Make Token',
      hint: { connector: 'Allows saving and filing items only. Works until you remove it here; no periodic replacement needed.',
        companion: 'Lets the companion on that computer answer questions with your AI app and install browser extension updates. It reads the library and can write notes and tasks through the AI, but cannot change library items.',
        mcp: 'Which computer or app will use it, so you know which one to remove later.' }[scope] });
    if (!name || !name.trim()) return;
    try {
      const res = await asAdmin(() => api.newAccessToken(name.trim(), scope));
      if (!res) return;
      if (scope === 'companion') refreshAiStatus();   // Ask AI appears in notes and the viewer
      fresh = { scope: res.entry.scope || scope, token: res.token, name: res.entry.name, index: 0, app: /codex/i.test(res.entry.name) ? 'codex' : 'claude' };
    } catch (err) { toastError(err); return; }
    refreshAiAccess(view);
  });
  view.querySelector('[data-ai-fresh]')!.addEventListener('click', (e) => {
    const target = e.target as Element;
    if (!fresh || !overview) return;
    const ep = overview.endpoints[fresh.index] || overview.endpoints[0];
    if (target.closest('[data-ai-copy-cmd]') && fresh.scope === 'companion') copy(companionCommand(ep ? ep.url.replace(/\/mcp\/?$/, '') : 'https://synapse.example.com'), 'Commands');
    else if (target.closest('[data-ai-copy-cmd]')) copy(command(fresh.app, ep ? ep.url : '', fresh.token), fresh.app === 'other' ? 'Address and header' : 'Command');
    else if (target.closest('[data-ai-app]')) {
      fresh.app = target.closest<HTMLElement>('[data-ai-app]')!.dataset.aiApp as App;
      renderFresh(view.querySelector<HTMLElement>('[data-ai-fresh]')!);
    }
    else if (target.closest('[data-ai-copy-token]')) copy(fresh.token, 'Token');
    else if (target.closest('[data-ai-done]')) { fresh = null; renderFresh(view.querySelector<HTMLElement>('[data-ai-fresh]')!); }
    else if (target.closest('[data-ai-addr]')) {
      fresh.index = Number(target.closest<HTMLElement>('[data-ai-addr]')!.dataset.aiAddr);
      renderFresh(view.querySelector<HTMLElement>('[data-ai-fresh]')!);
    }
  });
  view.querySelector('[data-ai-list]')!.addEventListener('click', async (e) => {
    const row = (e.target as Element).closest<HTMLElement>('[data-ai-token]');
    if (!row || !(e.target as Element).closest('[data-ai-remove]')) return;
    const tok = overview?.tokens.find((t) => t.id === row.dataset.aiToken);
    if (!tok) return;
    const ok = await confirmDialog(`Remove the token “${tok.name}”?`, {
      title: 'Remove token', confirm: 'Remove', danger: true, detail: 'The app using it loses access to the library at once.' });
    if (!ok) return;
    try { await asAdmin(() => api.removeAccessToken(tok.id)); refreshAiStatus(); } catch (err) { toastError(err); }
    refreshAiAccess(view);
  });
}
