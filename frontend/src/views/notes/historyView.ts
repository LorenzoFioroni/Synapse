// The notes history (kept by the server in Git, backend/notes/history/): the versions of the open
// note, and the changes to all notes with their milestones. A list on the left — the user's edits
// close together as one row, every AI change, rename or outside change a row of its own — and on
// the right exactly what the selected change did (lines, with the changed words marked), or how
// that version differs from the note now. A version is put back through the editor, so Cmd+Z
// takes it back again, and recorded as a restore.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { confirmDialog, openDialog, promptDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import type { HistoryCommit, HistoryDiff, HistoryEntry, HistoryStatus, HistoryVersion, Milestone, NoteHistory } from '../../types.js';
import { escapeHtml, formatDate, plural } from '../../util.js';
import { putBack } from './aiBar.js';
import { diffHtml, editsDialog } from './aiEdits.js';
import { groupEdits, kindIcon, span, whatHappened, whoMade, type Group } from './historyGroups.js';
import { ws } from './workspace.js';

const stem = (p: string) => p.split('/').pop()!.replace(/\.md$/i, '');

/** Why there is no history and how to get one (Git), as HTML. */
export function installHtml(st: Pick<HistoryStatus, 'reason'>): string {
  const mac = /Mac/.test(navigator.platform || navigator.userAgent);
  const win = /Win/.test(navigator.platform || navigator.userAgent);
  const how = mac ? 'open Terminal and run <span class="mono">xcode-select --install</span>'
    : win ? 'install Git from <span class="mono">git-scm.com</span>'
      : 'install the <span class="mono">git</span> package';
  return `${escapeHtml(st.reason || 'The history is off.')} The notes history needs Git: ${how}, then restart Synapse.`;
}

/** Tell any open history list that something was recorded (the change feed's ``history``). */
export function historyChanged(): void { window.dispatchEvent(new Event('zc:history')); }

function onHistory(dlg: Element, fn: () => void): void {
  const h = () => { if (!dlg.isConnected) window.removeEventListener('zc:history', h); else fn(); };
  window.addEventListener('zc:history', h);
}

function rowHtml(g: Group<HistoryEntry | HistoryCommit>, k: number, open: boolean, perNote: boolean, selected: string): string {
  const e = g.entries[0];
  const many = g.entries.length > 1;
  const what = perNote ? whatHappened(e as HistoryEntry) : e.summary;
  const marks = g.entries.flatMap((x) => x.milestones || []).map((m) => `<span class="h-mile" title="Milestone">${icon('flag', 'icon-sm')} ${escapeHtml(m.label)}</span>`).join('');
  const sub = many && open ? `<div class="h-sub">${g.entries.map((x, i) => `
      <button class="hist-row sub${selected === `${k}:${i}` ? ' selected' : ''}" data-g="${k}" data-i="${i}">
        <span class="h-what">${escapeHtml(span({ entries: [x] }))}</span></button>`).join('')}</div>` : '';
  return `<div class="h-group${many ? ' many' : ''}">
    <button class="hist-row by-${e.by}${selected === `${k}` ? ' selected' : ''}" data-g="${k}">
      <span class="h-icon">${icon(kindIcon(e.kind), 'icon-sm')}</span>
      <span class="h-main"><span class="h-what">${escapeHtml(what)}${many ? ` <span class="count">${plural(g.entries.length, 'version')}</span>` : ''}</span>
        <span class="h-meta">${escapeHtml(whoMade(e))} · ${escapeHtml(span(g))}</span>${marks ? `<span class="h-marks">${marks}</span>` : ''}</span>
      ${many ? `<span class="h-expand" data-expand="${k}" title="${open ? 'Hide the versions' : 'Show each version'}">${icon(open ? 'chevronDown' : 'chevron', 'icon-sm')}</span>` : ''}
    </button>${sub}</div>`;
}

// ------------------------------------------------------------------ one note

/** The history of the open note. */
export async function openNoteHistory(): Promise<void> {
  const session = ws.session;
  if (!session) return;
  const path = session.path;
  try {
    await session.save();
    await api.historyCheckpoint(path).catch(() => null);   // what was typed last is a version too
    const hist = await api.noteHistory(path, 300);
    if (hist.enabled === false) {
      // Without Git only AI changes are kept (the old way): that list, and how to get the rest.
      const st = await api.historyStatus();
      toast(installHtml(st), { timeout: 10000 });
      if (!hist.entries.length) return;
      const text = await editsDialog(hist, { savedHash: session.saved.hash, currentText: session.text });
      if (text !== null) await putBack(text);
      return;
    }
    await noteHistoryDialog(path, hist);
  } catch (e) { toastError(e); }
}

async function noteHistoryDialog(path: string, first: NoteHistory): Promise<void> {
  let hist = first;
  let groups = groupEdits(hist.entries);
  let sel = '0';
  let against: 'change' | 'now' = 'change';
  const expanded = new Set<number>();
  const versions = new Map<string, HistoryVersion>();
  const version = async (e: HistoryEntry) => {
    const key = `${e.id}:${e.path}`;
    if (!versions.has(key)) versions.set(key, await api.noteVersion(e.id, e.path, e.from_path));
    return versions.get(key)!;
  };
  const range = (): { newest: HistoryEntry; oldest: HistoryEntry } | null => {
    const [g, i] = sel.split(':').map(Number);
    const grp = groups[g];
    if (!grp) return null;
    if (i !== undefined && !isNaN(i)) return { newest: grp.entries[i] as HistoryEntry, oldest: grp.entries[i] as HistoryEntry };
    return { newest: grp.entries[0] as HistoryEntry, oldest: grp.entries[grp.entries.length - 1] as HistoryEntry };
  };
  let restoreBtn: HTMLButtonElement | null = null;
  let afterText: string | null = null;
  let picked: { text: string; id: string } | null = null;

  await openDialog({
    title: `History of ${stem(path)}`,
    subtitle: 'Every version of this note’s text. Your edits close together are grouped; select one to see what changed.',
    wide: true,
    render: (b, _close, buttons) => {
      b.closest('.dialog')!.classList.add('hist-dialog');
      restoreBtn = buttons[1];
      b.innerHTML = `<div class="hist">
        <div class="hist-list" role="listbox" aria-label="Versions"></div>
        <div class="hist-view"><div class="hist-head"></div><div class="hist-body"></div></div></div>`;
      const list = b.querySelector<HTMLElement>('.hist-list')!;
      const head = b.querySelector<HTMLElement>('.hist-head')!;
      const body = b.querySelector<HTMLElement>('.hist-body')!;
      const paintList = () => {
        list.innerHTML = groups.length
          ? groups.map((g, k) => rowHtml(g, k, expanded.has(k), true, sel)).join('')
          : '<div class="side-empty">No versions yet.</div>';
      };
      const show = async () => {
        paintList();
        const r = range();
        if (!r) { head.innerHTML = ''; body.innerHTML = ''; return; }
        const { newest, oldest } = r;
        const many = newest !== oldest;
        head.innerHTML = `<div class="hist-title"><b>${escapeHtml(whatHappened(newest))}</b>${many ? ` · ${plural(groups[Number(sel.split(':')[0])].entries.length, 'version')}` : ''}</div>
          <div class="h-meta">${escapeHtml(whoMade(newest))} · ${escapeHtml(formatDate(newest.at, true))}${newest.status === 'R' ? ` · was <span class="mono">${escapeHtml(newest.from_path || '')}</span>` : ''}</div>
          <div class="segmented hist-against" role="group">
            <button class="${against === 'change' ? 'active' : ''}" data-against="change">What changed</button>
            <button class="${against === 'now' ? 'active' : ''}" data-against="now">Compared with now</button></div>`;
        body.innerHTML = '<div class="side-empty">Loading…</div>';
        try {
          const [vNew, vOld] = await Promise.all([version(newest), version(oldest)]);
          if (range()?.newest !== newest) return;   // another row was picked meanwhile
          afterText = newest.status === 'D' ? null : vNew.after;
          const now = ws.session?.path === path ? ws.session.text : null;
          if (against === 'now') {
            body.innerHTML = afterText === null ? '<p class="muted">The note was deleted in this version.</p>'
              : now === null ? '<p class="muted">Open the note to compare.</p>'
                : afterText === now ? '<p class="muted">This version is the note as it is now.</p>'
                  : `<div class="diff-legend"><span class="d-del">− this version</span><span class="d-add">+ the note now</span></div>${diffHtml(afterText, now)}`;
          } else {
            const before = oldest.status === 'A' ? '' : vOld.before ?? '';
            const after = afterText ?? '';
            body.innerHTML = before === after
              ? `<p class="muted">${newest.status === 'R' ? `Renamed from <span class="mono">${escapeHtml(newest.from_path || '')}</span>; the text did not change.` : 'The text did not change.'}</p>`
              : `<div class="diff-legend"><span class="d-del">− before</span><span class="d-add">+ after</span></div>${diffHtml(before, after)}`;
          }
          if (restoreBtn) restoreBtn.disabled = afterText === null || afterText === now;
        } catch (e) { body.innerHTML = `<p class="form-error">${escapeHtml((e as Error).message)}</p>`; }
      };
      list.addEventListener('click', (ev) => {
        const t = ev.target as Element;
        const exp = t.closest<HTMLElement>('[data-expand]');
        if (exp) {
          const k = Number(exp.dataset.expand);
          if (expanded.has(k)) expanded.delete(k); else expanded.add(k);
        }
        const row = t.closest<HTMLElement>('.hist-row');
        if (!row) return;
        sel = row.dataset.i !== undefined ? `${row.dataset.g}:${row.dataset.i}` : row.dataset.g!;
        show();
      });
      list.addEventListener('keydown', (ev) => {
        if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
        ev.preventDefault();
        const rows = [...list.querySelectorAll<HTMLElement>('.hist-row')];
        const at = rows.findIndex((r) => r.classList.contains('selected'));
        const next = rows[Math.max(0, Math.min(rows.length - 1, at + (ev.key === 'ArrowDown' ? 1 : -1)))];
        if (next) { next.click(); list.querySelector<HTMLElement>('.hist-row.selected')?.focus(); }
      });
      head.addEventListener('click', (ev) => {
        const a = (ev.target as Element).closest<HTMLElement>('[data-against]')?.dataset.against as 'change' | 'now' | undefined;
        if (a && a !== against) { against = a; show(); }
      });
      onHistory(b, async () => {
        const top = hist.entries[0]?.id;
        hist = await api.noteHistory(path, 300);
        if (hist.entries[0]?.id === top) return;
        groups = groupEdits(hist.entries);
        sel = '0';
        expanded.clear();
        show();
      });
      show();
    },
    footerLeft: (() => {
      const btn = document.createElement('button');
      btn.className = 'btn btn-ghost';
      btn.innerHTML = `${icon('doc', 'icon-sm')} All Notes…`;
      btn.onclick = () => { btn.closest('.dialog-backdrop')?.querySelector<HTMLElement>('.dialog-footer .btn:not(.btn-ghost)')?.click(); openVaultHistory(); };
      return btn;
    })(),
    buttons: [
      { label: 'Close', value: null },
      {
        label: 'Restore This Version', kind: 'primary', value: 'restore',
        onClick: () => {
          const r = range();
          if (afterText === null || !r) return false;
          picked = { text: afterText, id: r.newest.id };
          return true;
        },
      },
    ],
  });
  if (picked && ws.session?.path === path) {
    const { text, id } = picked as { text: string; id: string };
    await putBack(text, id);
  }
}

// ------------------------------------------------------------------ all notes

const PAGE = 100;

/** Every change to the notes, and the milestones. */
export async function openVaultHistory(): Promise<void> {
  let log;
  try {
    await ws.session?.save();
    log = await api.historyLog(PAGE);
  } catch (e) { toastError(e); return; }
  if (!log.enabled) {
    await openDialog({ title: 'Notes History', render: (b) => { b.innerHTML = `<p>${installHtml(log)}</p>`; }, buttons: [{ label: 'OK', kind: 'primary' }] });
    return;
  }
  let entries: HistoryCommit[] = log.entries;
  let milestones: Milestone[] = log.milestones;
  let more = entries.length === PAGE;
  let groups = groupEdits(entries);
  let sel = groups.length ? 'g0' : '';
  const expanded = new Set<number>();

  await openDialog({
    title: 'Notes History',
    subtitle: 'Every change to your notes’ text, newest first. A milestone marks how all notes were at a moment (a submitted draft, say).',
    wide: true,
    render: (b) => {
      b.closest('.dialog')!.classList.add('hist-dialog');
      b.innerHTML = `<div class="hist">
        <div class="hist-list" role="listbox" aria-label="Changes"></div>
        <div class="hist-view"><div class="hist-head"></div><div class="hist-body"></div></div></div>`;
      const list = b.querySelector<HTMLElement>('.hist-list')!;
      const head = b.querySelector<HTMLElement>('.hist-head')!;
      const body = b.querySelector<HTMLElement>('.hist-body')!;
      const paintList = () => {
        const byCommit = new Map<string, Milestone[]>();
        for (const m of milestones) byCommit.set(m.commit, [...(byCommit.get(m.commit) || []), m]);
        const shown = new Set<string>();
        list.innerHTML = groups.map((g, k) => {
          const ms = g.entries.flatMap((e) => byCommit.get(e.id) || []);
          ms.forEach((m) => shown.add(m.id));
          return ms.map((m) => `<button class="hist-row milestone${sel === `m${m.id}` ? ' selected' : ''}" data-m="${escapeHtml(m.id)}">
              <span class="h-icon">${icon('flag', 'icon-sm')}</span><span class="h-main"><span class="h-what">${escapeHtml(m.label)}</span>
              <span class="h-meta">Milestone · ${escapeHtml(formatDate(m.at, true))}</span></span></button>`).join('')
            + rowHtml({ entries: g.entries.map((e) => ({ ...e, milestones: [] })) }, k, expanded.has(k), false, sel.startsWith('g') ? sel.slice(1) : '');
        }).join('')
          + milestones.filter((m) => !shown.has(m.id) && !more).map((m) => `<button class="hist-row milestone${sel === `m${m.id}` ? ' selected' : ''}" data-m="${escapeHtml(m.id)}">
              <span class="h-icon">${icon('flag', 'icon-sm')}</span><span class="h-main"><span class="h-what">${escapeHtml(m.label)}</span>
              <span class="h-meta">Milestone · ${escapeHtml(formatDate(m.at, true))}</span></span></button>`).join('')
          + (more ? '<button class="btn btn-sm btn-ghost hist-more" data-more>Show older changes</button>' : '')
          + (groups.length ? '' : '<div class="side-empty">Nothing recorded yet.</div>');
      };
      const filesHtml = (d: HistoryDiff) => d.files.length ? d.files.map((f) => {
        const label = ({ A: 'New', D: 'Deleted', R: 'Renamed', M: 'Changed' } as Record<string, string>)[f.status] || 'Changed';
        const text = f.before === undefined && f.after === undefined ? '<p class="muted">Too large to show here.</p>'
          : (f.before ?? '') === (f.after ?? '') ? `<p class="muted">${f.status === 'R' ? 'Only renamed.' : 'The text did not change.'}</p>`
            : diffHtml(f.before ?? '', f.after ?? '');
        return `<div class="hist-file"><div class="hist-file-head"><span class="badge">${label}</span>
            <button class="link-btn mono" data-open-note="${escapeHtml(f.path)}">${escapeHtml(f.path)}</button>
            ${f.from ? `<span class="muted">from <span class="mono">${escapeHtml(f.from)}</span></span>` : ''}</div>${text}</div>`;
      }).join('') + (d.cut ? '<p class="muted">More notes changed than can be shown here.</p>' : '')
        : '<p class="muted">No note differs.</p>';
      const show = async () => {
        paintList();
        body.innerHTML = '<div class="side-empty">Loading…</div>';
        try {
          if (sel.startsWith('m')) {
            const m = milestones.find((x) => x.id === sel.slice(1));
            if (!m) return;
            head.innerHTML = `<div class="hist-title"><b>${escapeHtml(m.label)}</b></div>
              <div class="h-meta">Milestone · ${escapeHtml(formatDate(m.at, true))}</div>
              <div class="hist-actions"><button class="btn btn-sm" data-m-restore>${icon('restore', 'icon-sm')} Restore All Notes…</button>
                <button class="btn btn-sm btn-ghost" data-m-rename>Rename…</button><button class="btn btn-sm btn-ghost" data-m-delete>Delete</button></div>`;
            const d = await api.historyDiff(m.id, 'now');
            if (sel !== `m${m.id}`) return;
            body.innerHTML = `<div class="hist-note">Changes since this milestone: − then, + now.</div>${filesHtml(d)}`;
            return;
          }
          const [gs, is] = sel.slice(1).split(':');
          const g = groups[Number(gs)];
          if (!g) return;
          const pickedEntries = is !== undefined ? [g.entries[Number(is)]] : g.entries;
          const newest = pickedEntries[0], oldest = pickedEntries[pickedEntries.length - 1];
          head.innerHTML = `<div class="hist-title"><b>${escapeHtml(newest.summary)}</b></div>
            <div class="h-meta">${escapeHtml(whoMade(newest))} · ${escapeHtml(formatDate(newest.at, true))}${pickedEntries.length > 1 ? ` · ${plural(pickedEntries.length, 'version')}` : ''}</div>`
            + (newest.body ? `<div class="hist-note">${escapeHtml(newest.body)}</div>` : '');
          const want = sel;
          const d = await api.historyDiff(`${oldest.id}^`, newest.id);
          if (sel !== want) return;
          body.innerHTML = filesHtml(d);
        } catch (e) { body.innerHTML = `<p class="form-error">${escapeHtml((e as Error).message)}</p>`; }
      };
      const reload = async () => {
        const n = Math.max(PAGE, entries.length);
        const res = await api.historyLog(n);
        entries = res.entries;
        milestones = res.milestones;
        more = entries.length === n;
        groups = groupEdits(entries);
      };
      list.addEventListener('click', async (ev) => {
        const t = ev.target as Element;
        if (t.closest('[data-more]')) {
          const res = await api.historyLog(PAGE, entries.length);
          entries = entries.concat(res.entries);
          more = res.entries.length === PAGE;
          groups = groupEdits(entries);
          paintList();
          return;
        }
        const exp = t.closest<HTMLElement>('[data-expand]');
        if (exp) {
          const k = Number(exp.dataset.expand);
          if (expanded.has(k)) expanded.delete(k); else expanded.add(k);
        }
        const row = t.closest<HTMLElement>('.hist-row');
        if (!row) return;
        sel = row.dataset.m ? `m${row.dataset.m}` : `g${row.dataset.i !== undefined ? `${row.dataset.g}:${row.dataset.i}` : row.dataset.g}`;
        show();
      });
      b.addEventListener('click', async (ev) => {
        const t = ev.target as Element;
        const open = t.closest<HTMLElement>('[data-open-note]')?.dataset.openNote;
        if (open) {
          b.closest('.dialog-backdrop')?.querySelector<HTMLElement>('.dialog-footer .btn:not(.btn-ghost)')?.click();
          const { openNotes } = await import('./notesView.js');
          openNotes(open);
          return;
        }
        const m = sel.startsWith('m') ? milestones.find((x) => x.id === sel.slice(1)) : null;
        if (!m) return;
        try {
          if (t.closest('[data-m-rename]')) {
            const label = await promptDialog('Name', { title: 'Rename Milestone', value: m.label, confirm: 'Rename' });
            if (!label?.trim()) return;
            await api.renameMilestone(m.id, label.trim());
            await reload();
            show();
          } else if (t.closest('[data-m-delete]')) {
            if (!(await confirmDialog(`Delete the milestone “${m.label}”?`, { title: 'Delete Milestone', confirm: 'Delete', danger: true, detail: 'Only the mark goes: every version stays in the history.' }))) return;
            await api.deleteMilestone(m.id);
            await reload();
            sel = groups.length ? 'g0' : '';
            show();
          } else if (t.closest('[data-m-restore]')) {
            if (!(await confirmDialog(`Put every note back as it was at “${m.label}”?`, {
              title: 'Restore Milestone', confirm: 'Restore', danger: true,
              detail: 'Changed notes get their text back, notes deleted since come back, and notes made since go to the notes trash. It is recorded as one more change, so nothing in between is lost.',
            }))) return;
            await Promise.all([...ws.views.values()].map((v) => v.session.save()));
            const r = await api.restoreMilestone(m.id);
            toast(`Restored ${plural(r.restored.length, 'note')}${r.trashed.length ? `; ${plural(r.trashed.length, 'newer note')} moved to the notes trash` : ''}.`, { type: 'success' });
            (await import('./notesView.js')).notesChanged();
            await reload();
            sel = 'g0';
            show();
          }
        } catch (e) { toastError(e); }
      });
      onHistory(b, async () => { await reload(); show(); });
      show();
    },
    footerLeft: (() => {
      const btn = document.createElement('button');
      btn.className = 'btn';
      btn.innerHTML = `${icon('flag', 'icon-sm')} Mark Milestone…`;
      btn.onclick = async () => {
        const m = await markMilestone();
        if (m) { sel = `m${m.id}`; historyChanged(); }
      };
      return btn;
    })(),
    buttons: [{ label: 'Close', value: null }],
  });
}

/** Name the state of all notes now. */
export async function markMilestone(): Promise<Milestone | null> {
  const label = await promptDialog('Name', {
    title: 'Mark Milestone', placeholder: 'Submitted to the journal', confirm: 'Mark',
    hint: 'How all your notes are now, under a name you can come back to.',
  });
  if (!label?.trim()) return null;
  try {
    await Promise.all([...ws.views.values()].map((v) => v.session.save()));
    const m = await api.addMilestone(label.trim());
    toast(`Milestone “${m.label}” marked.`, { type: 'success' });
    return m;
  } catch (e) { toastError(e); return null; }
}
