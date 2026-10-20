// Settings → AI Access: access tokens for AI apps (Claude Code) that use the library from another
// computer through the MCP endpoint, /mcp. See backend/access/tokens.py and backend/mcp/http.py.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { confirmDialog, promptDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import type { AccessTokensOverview } from '../../types.js';
import { escapeHtml, formatDate } from '../../util.js';
import { relTime } from './devices.js';

let overview: AccessTokensOverview | null = null;
/** A token just made: shown until Settings closes, because the server does not keep it. */
let fresh: { token: string; name: string; index: number } | null = null;

/** The command that adds Synapse to Claude Code with this token. */
function command(url: string, token: string): string {
  return `claude mcp add --scope user --transport http synapse ${url} --header "Authorization: Bearer ${token}"`;
}

function renderFresh(box: HTMLElement): void {
  if (!fresh || !overview) { box.innerHTML = ''; return; }
  const eps = overview.endpoints;
  const ep = eps[fresh.index] || eps[0];
  const choices = eps.length > 1 ? `<div class="pair-addrs">Address: ${eps.map((l, i) =>
    `<button class="token${i === fresh!.index ? ' active' : ''}" data-ai-addr="${i}">${escapeHtml(l.label)}</button>`).join(' ')}</div>` : '';
  box.innerHTML = `<div class="s-title">Token for ${escapeHtml(fresh.name)}</div>
    <div class="s-desc">Copy it now: Synapse keeps only a fingerprint and cannot show it again. On the other computer, run:</div>
    <pre class="mono ai-command" data-ai-command>${escapeHtml(command(ep ? ep.url : '<url>/mcp', fresh.token))}</pre>
    ${choices}
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-sm" data-ai-copy-cmd>${icon('copy')} Copy Command</button>
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
    : `Synapse only answers this computer. To use it from another computer, start it with <span class="mono">python server.py --lan</span>.${where}`)
    + (st.this_computer ? '' : ' Tokens are made on the computer running Synapse.');
  control.innerHTML = st.this_computer ? `<button class="btn" data-ai-new>${icon('plus')} New Token…</button>` : '';
  renderFresh(view.querySelector<HTMLElement>('[data-ai-fresh]')!);
  list.innerHTML = st.tokens.length ? st.tokens.map((t) => `<div class="backup-row" data-ai-token="${escapeHtml(t.id)}">
      <div class="b-main"><div class="b-when">${escapeHtml(t.name)}</div>
        <div class="b-what">Made ${escapeHtml(formatDate(t.created, true))} · last used ${escapeHtml(relTime(t.last_used))}${t.last_addr ? ` from ${escapeHtml(t.last_addr)}` : ''}</div></div>
      ${st.this_computer ? '<button class="btn btn-sm" data-ai-remove>Remove…</button>' : ''}</div>`).join('')
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
    if (!(e.target as Element).closest('[data-ai-new]')) return;
    const name = await promptDialog('Name', { title: 'New access token', placeholder: 'Work laptop — Claude Code', confirm: 'Make Token',
      hint: 'Which computer or app will use it, so you know which one to remove later.' });
    if (!name || !name.trim()) return;
    try {
      const res = await api.newAccessToken(name.trim());
      fresh = { token: res.token, name: res.entry.name, index: 0 };
    } catch (err) { toastError(err); return; }
    refreshAiAccess(view);
  });
  view.querySelector('[data-ai-fresh]')!.addEventListener('click', (e) => {
    const target = e.target as Element;
    if (!fresh || !overview) return;
    const ep = overview.endpoints[fresh.index] || overview.endpoints[0];
    if (target.closest('[data-ai-copy-cmd]')) copy(command(ep ? ep.url : '', fresh.token), 'Command');
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
    try { await api.removeAccessToken(tok.id); } catch (err) { toastError(err); }
    refreshAiAccess(view);
  });
}
