// Status for the visible workspace; background indexing still reports through notifications.
import { currentSource, folderById, on, state } from '../store.js';
import { startJob, type Job } from '../notifications/center.js';
import type { Counts } from '../types.js';
import { plural } from '../util.js';
import { tasksPage } from '../tasksPage.js';
import { notesOpen } from './notes/notesView.js';
import { ws } from './notes/workspace.js';
import { NOTE_STATUS } from './notes/sidePanel.js';
import { splitFrontmatter } from './notes/mdscan.js';
import { pageOpen } from './pages.js';

/* ------------------------------------------------------------ status bar */
export function initStatusBar() {
  const bar = document.getElementById('statusbar')!;
  bar.innerHTML = '<span data-s="count"></span><span data-s="sel"></span><span class="spacer"></span><span class="status-index" data-s="index"></span>';
  let tasks: { open: number; overdue: number } | null = null;
  let noteText: string | null = null, words = 0;
  const paint = () => {
    const count = bar.querySelector('[data-s="count"]')!;
    const selection = bar.querySelector('[data-s="sel"]')!;
    selection.textContent = '';
    if (notesOpen()) {
      const session = ws.session;
      if (!session) { count.textContent = 'No note open'; return; }
      if (noteText !== session.text) {
        noteText = session.text;
        words = (noteText.slice(splitFrontmatter(noteText).bodyStart).match(/\S+/gu) || [])
          .filter((token) => /[\p{L}\p{N}]/u.test(token)).length;
      }
      count.textContent = plural(words, 'word');
      selection.textContent = NOTE_STATUS[session.status][0];
      return;
    }
    if (tasksPage.isOpen()) {
      count.textContent = tasks ? `${plural(tasks.open, 'open task')}${tasks.overdue ? ` · ${tasks.overdue} overdue` : ''}` : '';
      return;
    }
    if (pageOpen()) { count.textContent = ''; return; }

    const filtered = state.query || state.tagFilter.size;
    let where = ({ trash: ' in Trash', duplicates: ' that look like duplicates' } as Record<string, string>)[state.view] || '';
    if (state.view.startsWith('folder:')) where = ` in ${folderById(state.view.slice(7))?.name || 'folder'}`;
    if (state.view.startsWith('smart:')) where = ` in ${state.sidebar.smart.find((s) => `smart:${s.id}` === state.view)?.name || 'smart folder'}`;
    if (state.view.startsWith('source:')) where = ` in ${currentSource()?.title || 'list'}`;
    const outside = state.view.startsWith('source:') ? state.items.filter((i) => i._outside).length : 0;
    const total = state.counts[state.view as keyof Counts] ?? state.items.length;
    bar.querySelector('[data-s="count"]')!.textContent = filtered
      ? `${plural(state.items.length, 'item')}${where} matching${state.query ? ` “${state.query}”` : ''}${state.tagFilter.size ? ` · tags: ${[...state.tagFilter].join(' + ')}` : ''}`
      : `${plural(state.view in state.counts ? total : state.items.length, 'item')}${where}${outside ? ` · ${outside} not in your library` : ''}`;
    bar.querySelector('[data-s="sel"]')!.textContent = state.selection.size > 1 ? `${state.selection.size} selected` : '';
  };
  on('items', paint);
  on('selection', paint);
  on('workspace', paint);
  on('note-status', paint);
  on('tasks-page', paint);
  on('tasks-count', (value) => { tasks = value; paint(); });
  paint();
  let indexing: Job | null = null;
  on('indexer', () => {
    const ix = state.indexer;
    const el = bar.querySelector('[data-s="index"]')!;
    if (ix?.indexer.running) {
      indexing ||= startJob('Indexing full text…');
      indexing.set(`Indexing full text ${ix.indexer.done}/${ix.indexer.total}`, ix.indexer.done, ix.indexer.total);
      el.textContent = '';
    } else { indexing?.finish('Full-text indexing complete'); indexing = null; el.textContent = ''; }
  });
  on('loading', (l: boolean) => bar.classList.toggle('loading', l));
}

