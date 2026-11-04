// arXiv Today in the app: dismissing papers from its feed, opening their PDF on arXiv, fetching
// now, and the morning's notification. The feed and its day variants are an item source (backend).

import type { PluginContext, PluginItem } from '../../../frontend/src/plugins/api.js';

interface Explain {
  score: number | null;
  tier?: string | null;
  earlier?: number | null;
  parts?: { id: string; label: string; value: number }[];
}

interface Model {
  learning: boolean;
  fitted_at: string | null;
  checked_at: string | null;
  labels: number;
  wanted: number;
  needed: number;
  weights: { id: string; label: string; default: number; learned: number | null }[];
}

// a paper shown this long in the item pane counts as opened (arrowing through the list does not)
const OPEN_AFTER_MS = 4000;
const TIERS: Record<string, string> = { read: 'Read', skim: 'Skim', rest: 'Rest' };

interface NewsResponse {
  news: { day: string; count: number; read: number; skim: number } | null;
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
  const opened = (arxiv: string) => { p.backend.post('/opened', { id: arxiv }).catch(() => undefined); };

  p.itemActions.register({
    id: 'pdf', title: 'Open PDF on arXiv', icon: 'pdf', outside: true,
    when: (items) => items.length === 1 && !!items[0]._outside && !!arxivOf(items[0]),
    run: (items) => {
      opened(arxivOf(items[0]));
      window.open(`https://arxiv.org/pdf/${arxivOf(items[0])}`, '_blank');
    },
  });

  // what the match rests on, one bar per kind of evidence (in log-odds, as the score adds them)
  const esc = p.ui.escapeHtml;
  function drawMatch(el: HTMLElement, r: Explain): void {
    if (r.score == null || !r.parts) {
      el.innerHTML = '<p class="at-dim">Not scored yet.</p>';
      return;
    }
    const top = Math.max(1, ...r.parts.map((x) => Math.abs(x.value)));
    const rows = r.parts.filter((x) => x.value !== 0).map((x) => `
      <div class="at-part">
        <span class="at-label">${esc(x.label)}</span>
        <span class="at-bar"><span class="${x.value < 0 ? 'at-neg' : ''}" style="width:${Math.round((Math.abs(x.value) / top) * 100)}%"></span></span>
        <span class="at-value">${x.value > 0 ? '+' : ''}${x.value.toFixed(1)}</span>
      </div>`).join('');
    const earlier = r.earlier != null ? ` <span class="at-dim">· earlier score ${esc(r.earlier)}</span>` : '';
    el.innerHTML = `<p class="at-head"><strong>${Math.round(r.score)}</strong> · ${esc(TIERS[r.tier || ''] || '')}${earlier}</p>
      ${rows || '<p class="at-dim">No evidence connects it to your library.</p>'}`;
  }

  p.itemPane.register({
    id: 'match', title: 'arXiv Match',
    when: (item) => fromLists([item]) && !!arxivOf(item),
    mount: (el, item) => {
      const arxiv = arxivOf(item);
      let alive = true;
      const timer = window.setTimeout(() => {
        if (alive && document.visibilityState === 'visible') opened(arxiv);
      }, OPEN_AFTER_MS);
      p.backend.get<Explain>('/explain', { id: arxiv })
        .then((r) => { if (alive) drawMatch(el, r); })
        .catch(() => { if (alive) el.textContent = ''; });
      return () => { alive = false; window.clearTimeout(timer); };
    },
  });

  // the weights in use, learned beside hand-set, with Learn Now and Reset
  p.settingsPage.register({
    id: 'model', title: 'Learned weights', shows: ['learn'],
    mount(el) {
      let gone = false;
      async function draw(): Promise<void> {
        const m = await p.backend.get<Model>('/model');
        if (gone) return;
        const when = m.fitted_at ? p.ui.formatDate(m.fitted_at, true) : null;
        const status = !m.learning ? 'Learning is off: the hand-set weights are used.'
          : when ? `Learned ${esc(when)} from ${m.labels} labels (${m.wanted} wanted).`
          : `Not learned yet: needs ${m.needed} papers you wanted and ${m.needed} you did not (${m.labels} labels so far, ${m.wanted} wanted).`;
        el.innerHTML = `
          <label class="at-toggle"><input type="checkbox" data-learn ${m.learning ? 'checked' : ''}> Learn from what I do</label>
          <p class="at-dim">${status}</p>
          <table class="at-weights"><thead><tr><th>Evidence</th><th>Hand-set</th><th>Learned</th></tr></thead><tbody>
          ${m.weights.map((w) => `<tr><td>${esc(w.label)}</td><td>${w.default}</td><td>${w.learned == null ? '–' : w.learned}</td></tr>`).join('')}
          </tbody></table>
          <div class="at-buttons"><button class="btn" data-now>Learn Now</button><button class="btn" data-reset>Reset to Hand-set</button></div>`;
        el.querySelector<HTMLInputElement>('[data-learn]')!.onchange = async (e) => {
          await p.saveSettings({ learn: (e.target as HTMLInputElement).checked }).catch(() => undefined);
          void draw();
        };
        el.querySelector<HTMLButtonElement>('[data-now]')!.onclick = async () => {
          await p.backend.post('/model/learn');
          p.notify('Learning from your feed…', { type: 'info', transient: true });
        };
        el.querySelector<HTMLButtonElement>('[data-reset]')!.onclick = async () => {
          if (!await p.ui.confirm('Use the hand-set weights again? The next night learns afresh from your labels.')) return;
          await p.backend.post('/model/reset');
          void draw();
        };
      }
      void draw();
      return () => { gone = true; };
    },
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
        const tiers = [n.read ? `${n.read} to read` : '', n.skim ? `${n.skim} to skim` : ''].filter(Boolean);
        p.notify(tiers.length ? `${papers}: ${tiers.join(', ')}` : papers, {
          type: 'success', sourceId: 'feed', desktop: true,
        });
      }
    } finally {
      asking = false;
    }
  }
  p.onChange((kinds) => { if (kinds.includes('sources')) news(); });
  news();
}
