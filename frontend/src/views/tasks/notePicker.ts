// Choosing a note (to show a task in it): a dialog with a filter over every note, most recently
// changed first.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { emit } from '../../store.js';
import type { NoteInfo } from '../../types.js';
import { openDialog } from '../../ui/dialog.js';
import { escapeHtml } from '../../util.js';
import { ws } from '../notes/workspace.js';

async function notes(): Promise<NoteInfo[]> {
  if (!ws.overview.notes.length) {
    try { ws.overview = await api.notes(); } catch { /* an empty list is shown */ }
  }
  return [...ws.overview.notes].sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
}

/** Resolves with the chosen note's path, or null. */
export async function pickNote(title = 'Show the task in a note'): Promise<string | null> {
  const list = await notes();
  let chosen: string | null = null;
  const value = await openDialog({
    title,
    render: (body, close) => {
      body.innerHTML = `<input class="input picker-filter" placeholder="Filter notes" spellcheck="false" autofocus>
        <div class="picker-list" role="listbox"></div>`;
      const input = body.querySelector<HTMLInputElement>('input')!;
      const box = body.querySelector<HTMLElement>('.picker-list')!;
      let shown: NoteInfo[] = [];
      let at = 0;
      const paint = () => {
        const q = input.value.trim().toLowerCase();
        shown = list.filter((n) => !q || (n.title || '').toLowerCase().includes(q) || n.path.toLowerCase().includes(q)).slice(0, 60);
        at = Math.min(at, Math.max(0, shown.length - 1));
        box.innerHTML = shown.length ? shown.map((n, i) => `<button type="button" class="picker-item${i === at ? ' active' : ''}" data-i="${i}">
            ${icon(n.type === 'paper' ? 'book' : 'doc')}<span class="nm">${escapeHtml(n.title || n.path)}</span><span class="muted">${escapeHtml(n.path)}</span></button>`).join('')
          : '<div class="muted picker-empty">No note matches.</div>';
      };
      input.addEventListener('input', () => { at = 0; paint(); });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          at = Math.max(0, Math.min(shown.length - 1, at + (e.key === 'ArrowDown' ? 1 : -1)));
          paint();
          box.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
        }
        if (e.key === 'Enter' && shown[at]) { e.preventDefault(); e.stopPropagation(); chosen = shown[at].path; close('ok'); }
      });
      box.addEventListener('click', (e) => {
        const b = (e.target as Element).closest<HTMLElement>('[data-i]');
        if (b) { chosen = shown[Number(b.dataset.i)].path; close('ok'); }
      });
      paint();
    },
    buttons: [{ label: 'Cancel', value: null }],
  });
  return value === 'ok' ? chosen : null;
}

/** Open a note at a line (the notes workspace takes over). */
export function openNoteAt(path: string, line?: number | null): void {
  emit('open-note', { path, line: line ?? null });
}
