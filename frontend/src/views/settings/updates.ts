// Settings → Updates: the version that runs, the newer commits on the GitHub branch Synapse comes
// from, Check for Updates, and Update Now where the server has an updater (backend/online/updates.py,
// docs/updates.md). Installing replaces the code on the server, so another device confirms with a
// passkey first, as for plugins.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import type { UpdateStatus } from '../../types.js';
import { confirmDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml, formatDate, plural } from '../../util.js';
import { asAdmin } from './confirm.js';
import { setNavBadge } from './page.js';

const POLL_MS = 3000;   // while an update is installed: is the new version answering yet?
const GIVE_UP_MS = 15 * 60_000;

const mono = (text: string) => `<span class="mono">${escapeHtml(text)}</span>`;

function leadHtml(st: UpdateStatus): string {
  if (!st.source) return 'This Synapse does not check for updates: its server does not say where its code comes from.';
  return `Synapse looks for new code on the <a href="${escapeHtml(st.source.url)}" target="_blank" rel="noopener">${escapeHtml(st.source.branch)}</a>
    branch of ${mono(st.source.repo)} on GitHub every 6 hours, and the bell tells you when there is some.`;
}

function versionHtml(st: UpdateStatus): string {
  const running = st.current ? `Running ${mono(st.current.short)}` : 'Running a build that does not say which commit it is';
  const checked = st.available ? (st.checked ? ` · checked ${escapeHtml(formatDate(st.checked, true))}` : ' · not checked yet') : '';
  return running + checked + (st.error ? `<div class="form-error">${escapeHtml(st.error)}</div>` : '');
}

/** How to update by hand, where the server has no updater. */
function byHandHtml(st: UpdateStatus): string {
  return st.container
    ? `To update, run ${mono('docker compose up -d --build')} in the server's Synapse folder.`
    : `To update, run ${mono('git pull')} in Synapse's folder, then start it again.`;
}

function commitRow(c: UpdateStatus['commits'][number]): string {
  const title = c.url ? `<a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(c.title || c.short)}</a>` : escapeHtml(c.title || c.short);
  return `<div class="backup-row"><div class="b-main"><div class="b-when">${title}</div>
    <div class="b-what">${mono(c.short)}${c.date ? ` · ${escapeHtml(formatDate(c.date, true))}` : ''}</div></div></div>`;
}

function bodyHtml(st: UpdateStatus): string {
  const job = st.install;
  if (job?.state === 'running') {
    return `<div class="s-desc">${icon('refresh', 'icon-sm')} ${escapeHtml(job.message || 'Installing the update…')}
      Synapse restarts in about a minute; this page reloads by itself.</div>`;
  }
  const out: string[] = [];
  if (job?.state === 'failed' && job.commit !== st.current?.commit) {
    out.push(`<div class="form-error">The last update failed: ${escapeHtml(job.message)}</div>`);
    if (job.log?.length) out.push(`<details><summary class="s-desc">Details</summary><pre class="mono" style="white-space:pre-wrap;font-size:var(--fs-xs)">${escapeHtml(job.log.join('\n'))}</pre></details>`);
  }
  if (!st.available) {
    out.push(`<div class="s-desc">${byHandHtml(st)}</div>`);
    return out.join('');
  }
  const install = (what: string) => st.can_install
    ? `<div style="display:flex;align-items:center;gap:12px;margin-top:8px"><div class="s-desc" style="flex:1">Synapse makes a backup, installs ${what} and restarts: it is offline for about a minute.</div>
        <button class="btn btn-primary" data-update-install>${icon('download')} Update Now</button></div>`
    : `<div class="s-desc">${byHandHtml(st)}</div>`;
  const latest = st.latest;
  if (st.behind) {
    const more = st.behind > st.commits.length ? `, the newest ${st.commits.length} below` : '';
    const compare = st.compare_url ? ` (<a href="${escapeHtml(st.compare_url)}" target="_blank" rel="noopener">all changes</a>)` : '';
    out.push(`<div class="s-desc">${escapeHtml(plural(st.behind, 'new commit'))} on ${mono(st.source!.branch)}${more}${compare}.`
      + (st.diverged ? ` This build also has changes that are not on ${mono(st.source!.branch)}; updating replaces them.` : '') + '</div>');
    out.push(`<div class="backup-list">${st.commits.map(commitRow).join('')}</div>`);
    out.push(install(latest ? mono(latest.short) : 'the newest version'));
  } else if (st.behind === 0) {
    out.push('<div class="s-desc">This is the newest version.</div>');
  } else if (latest) {
    out.push(`<div class="s-desc">The newest commit on ${mono(st.source!.branch)} is ${mono(latest.short)}${latest.title ? ` (${escapeHtml(latest.title)})` : ''}.</div>`);
    out.push(install(mono(latest.short)));
  }
  return out.join('');
}

function paint(view: HTMLElement, st: UpdateStatus): void {
  view.querySelector<HTMLElement>('[data-update-lead]')!.innerHTML = leadHtml(st);
  view.querySelector<HTMLElement>('[data-update-desc]')!.innerHTML = versionHtml(st);
  view.querySelector<HTMLElement>('[data-update-body]')!.innerHTML = bodyHtml(st);
  view.querySelector<HTMLButtonElement>('[data-update-check]')!.hidden = !st.available;
  setNavBadge(view, 'updates', st.behind ? 'New' : '');
}

let shown: UpdateStatus | null = null;
let waiting = false;

/** Show the update status in the open Settings page ``view``; ``refresh`` asks GitHub first. */
export async function refreshUpdates(view: HTMLElement, refresh = false): Promise<void> {
  try {
    const st = await api.updates(refresh);
    if (!view.isConnected) return;
    shown = st;
    paint(view, st);
    if (st.install?.state === 'running') void waitForNewVersion(view, st.install.commit ?? null);
  } catch (e) { view.querySelector<HTMLElement>('[data-update-desc]')!.textContent = (e as Error).message; }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** While the server rebuilds and restarts: wait until it answers with ``target`` (or the updater gave up). */
async function waitForNewVersion(view: HTMLElement, target: string | null): Promise<void> {
  if (waiting) return;
  waiting = true;
  try {
    for (const until = Date.now() + GIVE_UP_MS; Date.now() < until;) {
      await sleep(POLL_MS);
      let st: UpdateStatus;
      try { st = await api.updates(); } catch { continue; }   // restarting
      const arrived = !!target && st.current?.commit === target;
      if (arrived || (st.install && st.install.state !== 'running')) {
        if (arrived || st.install?.state === 'done') {
          if (view.isConnected) { toast('Synapse was updated. Reloading…', { type: 'success' }); setTimeout(() => location.reload(), 800); }
          else toast('Synapse was updated. Reload the page to use the new version.', { type: 'success', action: { label: 'Reload', run: () => location.reload() } });
        } else if (view.isConnected) paint(view, st);
        return;
      }
      if (view.isConnected) paint(view, st);
    }
  } finally { waiting = false; }
}

async function install(view: HTMLElement, button: HTMLButtonElement): Promise<void> {
  const target = shown?.latest?.short;
  if (!(await confirmDialog(target ? `Update Synapse to ${target}?` : 'Update Synapse to the newest version?', {
    title: 'Update Synapse', confirm: 'Update',
    detail: 'Synapse makes a backup first, then installs the new version and restarts. It is offline for about a minute; this page reloads by itself.',
  }))) return;
  button.disabled = true;
  try {
    // a passkey confirmation first when this is not the computer Synapse runs on
    const res = await asAdmin(() => api.installUpdate());
    if (!res) { toast('Not updated: updating from another device needs your passkey.', { type: 'error' }); return; }
    shown = res;
    if (res.started.state === 'current') { toast(res.started.message, { type: 'success' }); paint(view, res); return; }
    paint(view, { ...res, install: res.started });
    void waitForNewVersion(view, res.started.commit ?? null);
  } catch (e) { toastError(e); } finally { button.disabled = false; }
}

export function bindUpdates(view: HTMLElement): void {
  view.querySelector<HTMLButtonElement>('[data-update-check]')!.onclick = async (e) => {
    const b = e.currentTarget as HTMLButtonElement;
    b.disabled = true;
    await refreshUpdates(view, true);
    b.disabled = false;
  };
  view.querySelector<HTMLElement>('[data-update-body]')!.addEventListener('click', (e) => {
    const b = (e.target as Element).closest<HTMLButtonElement>('[data-update-install]');
    if (b) void install(view, b);
  });
}
