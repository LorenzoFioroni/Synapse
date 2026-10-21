// Recently Added's frontend: a page listing what was added in the last 30 days (with today's count
// on its sidebar entry), a command to open it, a section in the item pane saying when the item was
// added, and an item action to forget that. Plain JavaScript, loaded as an ES module.
// The types are in Synapse's frontend/src/plugins/api.ts (copy it next to this file to have them
// checked by your editor).

/** @param {import('./api').PluginContext} p */
export function activate(p) {
  const { escapeHtml, formatDate } = p.ui;
  const today = () => new Date().toISOString().slice(0, 10);

  async function recent() {
    const { items } = await p.backend.get('/recent', { days: 30 });
    p.pages.setBadge('recent', items.filter((i) => i.added_at.startsWith(today())).length || null);
    return items;
  }

  async function draw(el) {
    const items = await recent();
    el.innerHTML = items.length
      ? `<ul class="ra-list">${items.map((i) => `<li>
          <button class="ra-item" data-id="${escapeHtml(i.item_id)}">${escapeHtml(i.title || 'Untitled')}</button>
          <span class="ra-when">${escapeHtml(formatDate(i.added_at, true))}</span></li>`).join('')}</ul>`
      : '<p class="ra-empty">Nothing was added in the last 30 days.</p>';
  }

  p.pages.register({
    id: 'recent',
    title: 'Recently Added',
    icon: 'calendar',
    mount(el) {
      draw(el);
      el.addEventListener('click', (e) => {
        const b = e.target.closest('[data-id]');
        if (b) p.library.show(b.dataset.id);
      });
      return p.onChange((kinds) => { if (kinds.includes('library')) draw(el); });
    },
  });

  p.commands.register({ id: 'show', title: 'Show Recently Added', key: 'Mod-Alt-r', run: () => p.pages.open('recent') });

  p.itemPane.register({
    id: 'added',
    title: 'Added',
    mount(el, item) {
      p.backend.get(`/item/${encodeURIComponent(item.id)}`).then((r) => {
        el.textContent = r.added_at ? `Added ${formatDate(r.added_at, true)}.` : 'Added before Recently Added was turned on.';
      });
    },
  });

  p.itemActions.register({
    id: 'forget',
    title: 'Forget When It Was Added',
    icon: 'close',
    async run(items) {
      await p.backend.post('/forget', { ids: items.map((i) => i.id) });
      p.notify(items.length === 1 ? 'Forgotten.' : `Forgot ${items.length} items.`, { type: 'success' });
      recent();
    },
  });

  // the sidebar's count, also before the page is opened
  recent();
  p.onChange((kinds) => { if (kinds.includes('library')) recent(); });
}
