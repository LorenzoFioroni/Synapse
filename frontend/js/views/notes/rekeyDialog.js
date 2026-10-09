// Preview shown before a citation key is changed by hand: which notes will be updated.
import { api } from '../../api.js';
import { openDialog } from '../../ui/dialog.js';
import { escapeHtml, plural } from '../../util.js';

/** Resolves true to go ahead (also when no note is affected), false to cancel. */
export async function confirmKeyChange(oldKey, newKey) {
  if (!oldKey || !newKey || oldKey === newKey) return true;
  let plan;
  try { plan = await api.renamePlan(oldKey, newKey); } catch { return true; }   // notes off or unreachable
  if (!plan.notes.length && !plan.moves.length) return true;
  const v = await openDialog({
    title: 'Update notes too?',
    subtitle: `${plural(plan.notes.length, 'note')} cite <span class="mono">@${escapeHtml(oldKey)}</span>. They will be changed to <span class="mono">@${escapeHtml(newKey)}</span>, after a backup of your notes is saved.`,
    wide: true,
    render: (b) => {
      b.innerHTML = `<div class="rekey-list">${plan.notes.map((n) => `
        <div class="rekey-note"><div class="rekey-title">${escapeHtml(n.title)} <span class="muted mono">${escapeHtml(n.path)}</span></div>
          ${n.changes.slice(0, 6).map((c) => `<div class="rekey-line"><span class="ln">${c.line}</span><span class="d-del">${escapeHtml(c.before)}</span><span class="d-add">${escapeHtml(c.after)}</span></div>`).join('')}
          ${n.changes.length > 6 ? `<div class="muted">… and ${n.changes.length - 6} more lines</div>` : ''}</div>`).join('')}
        ${plan.moves.map((m) => `<div class="rekey-move">Paper note renamed: <span class="mono">${escapeHtml(m.from)}</span> → <span class="mono">${escapeHtml(m.to)}</span></div>`).join('')}</div>`;
    },
    buttons: [{ label: 'Cancel', value: false }, { label: `Change key and update ${plural(plan.notes.length, 'note')}`, kind: 'primary', value: true }],
  });
  return v === true;
}
