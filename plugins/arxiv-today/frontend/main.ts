// arXiv Today in the app: dismissing papers from its feed, opening their PDF on arXiv, fetching
// now, and the morning's notification. The feed and its day variants are an item source (backend).

import type { PluginContext, PluginItem } from '../../../frontend/src/plugins/api.js';

interface NewsResponse {
  news: { day: string; count: number; connected: number } | null;
}

export function activate(p: PluginContext): void {
  const fromLists = (items: PluginItem[]) =>
    items.length > 0 && items.every((i) => String(i._source || '').startsWith(`${p.id}.`));
  const arxivOf = (i: PluginItem) => String(i.arxiv || /^arxiv:(.+)$/i.exec(String(i.archive_id || ''))?.[1] || '');

  async function dismiss(items: PluginItem[], undo = false): Promise<void> {
    const ids = items.map(arxivOf).filter(Boolean);
    if (!ids.length) return;
    await p.backend.post('/dismiss', { ids, undo });
    await p.sources.refresh();
    if (!undo) {
      p.notify(ids.length === 1 ? 'Dismissed one paper' : `Dismissed ${ids.length} papers`, {
        action: { label: 'Undo', run: () => dismiss(items, true) }, transient: true,
      });
    }
  }

  p.itemActions.register({
    id: 'dismiss', title: 'Dismiss from arXiv Today', icon: 'close', outside: true,
    when: fromLists, run: (items) => dismiss(items),
  });
  p.itemActions.register({
    id: 'pdf', title: 'Open PDF on arXiv', icon: 'pdf', outside: true,
    when: (items) => items.length === 1 && !!items[0]._outside && !!arxivOf(items[0]),
    run: (items) => { window.open(`https://arxiv.org/pdf/${arxivOf(items[0])}`, '_blank'); },
  });
  p.commands.register({
    id: 'dismiss', title: 'arXiv: Dismiss Selected Papers', key: 'Mod-Alt-d',
    when: () => fromLists(p.library.selected()), run: () => dismiss(p.library.selected()),
  });
  p.commands.register({ id: 'show', title: 'Show arXiv Feed', key: 'Mod-Alt-t', run: () => p.sources.open('feed') });
  p.commands.register({
    id: 'fetch', title: 'arXiv: Fetch Today’s Papers Now',
    run: async () => {
      await p.backend.post('/fetch');
      p.notify('Fetching today’s papers…', { type: 'info' });
    },
  });

  // the morning's notification, once a day's papers are scored (in one window: the first to ask)
  let asking = false;
  async function news(): Promise<void> {
    if (asking) return;
    asking = true;
    try {
      const r = await p.backend.post<NewsResponse>('/news');
      const n = r.news;
      if (n && n.count) {
        const papers = n.count === 1 ? 'One new paper' : `${n.count} new papers`;
        const connected = n.connected ? `, ${n.connected} connected to your library` : '';
        p.notify(`${papers}${connected}`, { type: 'success', sourceId: 'feed', desktop: true });
      }
    } finally {
      asking = false;
    }
  }
  p.onChange((kinds) => { if (kinds.includes('sources')) news(); });
  news();
}
