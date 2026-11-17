// Settings → AI Access: separate tokens for MCP clients and the companion, with shared
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
let fresh: { token: string; name: string; index: number; app: App; scope: 'mcp' | 'companion' } | null = null;

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

/** What to run on the computer that has the AI app, once the program is downloaded. The token is
 *  pasted into the companion's window, so it is in no command line (shell history, process list). */
const COMPANION_COMMAND = 'python3 synapse_companion.pyz';

function renderFresh(box: HTMLElement): void {
  if (!fresh || !overview) { box.innerHTML = ''; return; }
  const eps = overview.endpoints;
  const ep = eps[fresh.index] || eps[0];
  if (fresh.scope === 'companion') {
    const server = ep ? ep.url.replace(/\/mcp\/?$/, '') : 'https://synapse.example.com';
    box.innerHTML = `<div class="s-title">Companion token for ${escapeHtml(fresh.name)}</div>
      <div class="s-desc">Copy the token now: Synapse keeps only a fingerprint and cannot show it again. On the computer that has your AI app
      (it needs Python 3), download the companion and start it once with the command below. In the window that opens, enter this
      server’s address and the token. The companion then starts with the computer: “Ask AI” appears in notes and in the PDF viewer,
      and it installs the browser extension and passes its saves to Synapse.</div>
      <a class="btn btn-sm" href="/api/ai/companion-script" download="synapse_companion.pyz" data-ai-download>${icon('download')} Download synapse_companion.pyz</a>
      <pre class="mono ai-command" data-ai-command>${escapeHtml(COMPANION_COMMAND)}</pre>
      <div class="s-desc">Address: <span class="mono" data-ai-server>${escapeHtml(server)}</span></div>
      <pre class="mono ai-command" data-ai-token-text>${escapeHtml(fresh.token)}</pre>
      ${eps.length > 1 ? `<div class="pair-addrs">Address: ${eps.map((l, i) => `<button class="token${i === fresh!.index ? ' active' : ''}" data-ai-addr="${i}">${escapeHtml(l.label)}</button>`).join(' ')}</div>` : ''}
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-sm" data-ai-copy-server>${icon('copy')} Copy Address</button>
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
  control.innerHTML = admin ? `<button class="btn" data-ai-new>${icon('plus')} New Token…</button> <button class="btn" data-companion-new>${icon('plus')} New Companion Token…</button>` : '';
  renderFresh(view.querySelector<HTMLElement>('[data-ai-fresh]')!);
  list.innerHTML = st.tokens.length ? st.tokens.map((t) => `<div class="backup-row" data-ai-token="${escapeHtml(t.id)}">
      <div class="b-main"><div class="b-when">${escapeHtml(t.name)}</div>
        <div class="b-what">${t.scope === 'connector' ? 'Browser token · no longer used, the extension goes through the companion' : t.scope === 'companion' ? 'Companion · local AI and browser extension' : 'AI app · MCP access'}</div>
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
    if (!target.closest('[data-ai-new], [data-companion-new]')) return;
    const scope = target.closest('[data-companion-new]') ? 'companion' : 'mcp';
    const name = await promptDialog('Name', {
      title: { companion: 'New companion token', mcp: 'New access token' }[scope],
      placeholder: { companion: 'Work laptop', mcp: 'Work laptop — Codex' }[scope], confirm: 'Make Token',
      hint: { companion: 'Lets the companion on that computer answer questions with your AI app, and save papers and PDFs from the browser extension there. The AI reads the library and can write notes and tasks, but cannot change library items.',
        mcp: 'Which computer or app will use it, so you know which one to remove later.' }[scope] });
    if (!name || !name.trim()) return;
    try {
      const res = await asAdmin(() => api.newAccessToken(name.trim(), scope));
      if (!res) return;
      if (scope === 'companion') refreshAiStatus();   // Ask AI appears in notes and the viewer
      fresh = { scope, token: res.token, name: res.entry.name, index: 0, app: /codex/i.test(res.entry.name) ? 'codex' : 'claude' };
    } catch (err) { toastError(err); return; }
    refreshAiAccess(view);
  });
  view.querySelector('[data-ai-fresh]')!.addEventListener('click', (e) => {
    const target = e.target as Element;
    if (!fresh || !overview) return;
    const ep = overview.endpoints[fresh.index] || overview.endpoints[0];
    if (target.closest('[data-ai-copy-server]')) copy(ep ? ep.url.replace(/\/mcp\/?$/, '') : '', 'Address');
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
