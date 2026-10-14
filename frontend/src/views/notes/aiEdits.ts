// @ts-nocheck
// Changes Claude made to notes through the MCP server's edit_note (issue #40).
//
// The server keeps the version each change replaced (GET /api/notes/history). The open note
// shows a bar while it is exactly what Claude left — "Claude edited this note … Show changes ·
// Undo" — and the note's details list every change Claude made, each with its diff and a way
// back. Undoing goes through the editor like typing would (so Cmd+Z / Cmd+Shift+Z work on it
// and the normal hash-checked save writes it); when the change arrives in an open note, the
// editor takes it as one undo step, so Cmd+Z alone reverts it too.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { openDialog } from '../../ui/dialog.js';
import { escapeHtml, formatDate, modLabel, plural, storage, store } from '../../util.js';
import { compactDiff, lineDiff } from './diff.js';

const SEEN = 'zc.notes.aiSeen';

/** {path, hash, entries: [{id, at, by, summary, before_hash, after_hash}]} newest first, or null. */
export async function editsOf(path) {
  try { return await api.noteHistory(path); } catch { return null; }
}

const seen = () => new Set(storage(SEEN, []));
export function markSeen(id) {
  const s = [...seen()].filter((x) => x !== id);
  s.push(id);
  store(SEEN, s.slice(-300));
}

/** The change to point out in the open note: Claude's latest, if the note is still exactly its result. */
export function pendingEdit(hist, savedHash) {
  const e = hist?.entries?.[0];
  return e && e.by === 'ai' && e.after_hash === savedHash && !seen().has(e.id) ? e : null;
}

function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!(s >= 0)) return formatDate(iso, true);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return formatDate(iso, true);
}

export function barHtml(e) {
  return `${icon('sparkle', 'icon-sm')}
    <span class="ai-msg" title="${escapeHtml(e.summary || '')}"><b>Claude edited this note</b> ${escapeHtml(ago(e.at))}${e.summary ? ` — ${escapeHtml(e.summary)}` : ''}</span>
    <button class="btn btn-sm btn-ghost" data-act="ai-show" data-ai-id="${escapeHtml(e.id)}">Show changes</button>
    <button class="btn btn-sm" data-act="ai-undo" data-ai-id="${escapeHtml(e.id)}" title="Or press ${modLabel}Z in the note">Undo</button>
    <button class="icon-btn" data-act="ai-dismiss" data-ai-id="${escapeHtml(e.id)}" title="Keep the change and hide this" aria-label="Hide">${icon('close', 'icon-sm')}</button>`;
}

/** A line diff as HTML (also used by the conflict dialog). */
export function diffHtml(a, b) {
  const rows = lineDiff(a, b);
  if (rows === null) return '<p class="muted">The notes are too long to compare here.</p>';
  return `<div class="diff">${compactDiff(rows).map((r) => (r.op === '…'
    ? `<div class="d-gap">… ${plural(r.count, 'unchanged line')} …</div>`
    : `<div class="d-${r.op === '+' ? 'add' : r.op === '-' ? 'del' : 'same'}"><span class="d-op">${r.op === ' ' ? '' : r.op}</span>${escapeHtml(r.text) || ' '}</div>`)).join('')}</div>`;
}

/**
 * One change: what it did to the note (when the note is still its result) or how the note now
 * differs from the version before it. Resolves with the text to put back, or null.
 */
export async function showEdit(entry, { savedHash, currentText }) {
  const v = await api.noteVersion(entry.id);
  const current = entry.after_hash === savedHash;
  return openDialog({
    title: current ? 'Claude’s change' : 'Before Claude’s change',
    subtitle: `${escapeHtml(formatDate(entry.at, true))}${entry.summary ? ` · ${escapeHtml(entry.summary)}` : ''}${current ? ''
      : '<br>The note was changed after this. Below: the note now (+) against the version before Claude’s change (−).'}`,
    wide: true,
    render: (b) => {
      b.innerHTML = `<div class="diff-legend"><span class="d-del">− before Claude’s change</span><span class="d-add">+ ${current ? 'after it (the note now)' : 'the note now'}</span></div>
        ${diffHtml(v.before, currentText)}`;
    },
    buttons: [{ label: 'Close', value: null },
      { label: current ? 'Undo this change' : 'Restore the version before it', kind: 'primary', value: 'restore' }],
  }).then((r) => (r === 'restore' ? v.before : null));
}

/** Every change Claude made to the note. Resolves with the text to put back, or null. */
export async function editsDialog(hist, { savedHash, currentText }) {
  let picked = null;
  await openDialog({
    title: 'Claude’s edits of this note',
    subtitle: `Each change keeps the version it replaced. Restoring one puts that version in the editor (${modLabel}Z takes it back).`,
    wide: true,
    render: (b) => {
      b.innerHTML = `<div class="ai-list">${hist.entries.map((e) => `
        <div class="ai-row">
          <div class="ai-when">${escapeHtml(formatDate(e.at, true))}${e.after_hash === savedHash ? ' <span class="badge accent">the note now</span>' : ''}</div>
          <div class="ai-sum">${escapeHtml(e.summary || 'Edited by Claude')}</div>
          <button class="btn btn-sm" data-ai-id="${escapeHtml(e.id)}">Changes…</button>
        </div>`).join('')}</div>`;
      b.addEventListener('click', async (ev) => {
        const id = ev.target.closest('[data-ai-id]')?.dataset.aiId;
        const e = id && hist.entries.find((x) => x.id === id);
        if (!e) return;
        const text = await showEdit(e, { savedHash, currentText });
        if (text !== null) {
          picked = text;
          b.closest('.dialog-backdrop')?.querySelector('.dialog-footer .btn')?.click();
        }
      });
    },
    buttons: [{ label: 'Close', value: null }],
  });
  return picked;
}
