// Metadata Check in the app: Run Now (command and button), ignoring an item's suggestions (item
// menu and command) and, in Settings, the list of ignored suggestions with Stop Ignoring.

/** @typedef {import('../../../frontend/src/plugins/api').PluginContext} PluginContext */
/** @typedef {import('../../../frontend/src/plugins/api').PluginItem} PluginItem */

/** @param {PluginContext} p */
export function activate(p) {
  const fieldId = `${p.id}.metadata_check`;
  /** @param {PluginItem} item */
  const verdictOf = (item) => {
    const f = /** @type {Record<string, unknown> | undefined} */ (item._f);
    return String((f && f[fieldId]) || '');
  };
  /** @param {PluginItem} item */
  const suggests = (item) => /^(Review:|Published version:)/.test(verdictOf(item));
  /** @param {PluginItem[]} items */
  const anySuggestion = (items) => items.length > 0 && !items.some((i) => i._outside) && items.some(suggests);
  /** @param {PluginItem[]} items */
  const anyIgnored = (items) => items.length > 0 && !items.some((i) => i._outside) && items.some((i) => verdictOf(i).includes('· ignored'));

  async function runNow() {
    /** @type {{ items: number }} */
    const r = await p.backend.post('/run');
    p.notify(`Checking ${r.items === 1 ? 'one item' : `${r.items} items`} in the background…`, { type: 'info', transient: true });
  }

  /** @param {string[]} ids */
  async function unignore(ids) {
    await p.backend.post('/unignore', { ids });
  }

  /** @param {PluginItem[]} items */
  async function ignore(items) {
    const ids = items.filter(suggests).map((i) => i.id);
    if (!ids.length) return;
    /** @type {{ ignored: string[], pending: string[] }} */
    const r = await p.backend.post('/ignore', { ids });
    const n = r.ignored.length + r.pending.length;
    p.notify(n === 1 ? 'Ignoring this suggestion' : `Ignoring suggestions for ${n} items`, {
      transient: true,
      action: { label: 'Undo', run: () => { unignore(ids); } },
    });
  }

  // the palette shows it as "Metadata Check: Run Now"; the item actions below are in it too
  p.commands.register({ id: 'run', title: 'Run Now', run: runNow });
  p.itemActions.register({
    id: 'ignore', title: 'Ignore Metadata Suggestion', icon: 'close',
    when: anySuggestion, run: (items) => ignore(items),
  });
  p.itemActions.register({
    id: 'unignore', title: 'Stop Ignoring Metadata Suggestion',
    when: anyIgnored, run: (items) => unignore(items.map((i) => i.id)),
  });

  p.settingsPage.register({
    id: 'status', title: p.name,
    mount(el) {
      let gone = false;
      const esc = p.ui.escapeHtml;
      async function draw() {
        /** @typedef {{ id: string, title: string, what: string[], pending: boolean, in_library: boolean }} Ignored */
        const [st, ig] = await Promise.all([
          p.backend.get('/status').then((r) => /** @type {{ last_checked: number | null }} */ (r)),
          p.backend.get('/ignored').then((r) => /** @type {{ ignored: Ignored[] }} */ (r)),
        ]);
        if (gone) return;
        const last = st.last_checked ? p.ui.formatDate(new Date(st.last_checked * 1000).toISOString(), true) : 'not yet';
        const rows = ig.ignored.map((e) => `
          <li class="mc-ignored" data-id="${esc(e.id)}">
            <div class="mc-what"><strong>${esc(e.title)}</strong>${e.in_library ? '' : ' <span class="mc-dim">(no longer in the library)</span>'}
              <div class="mc-dim">${e.pending ? 'Whatever the next check suggests' : esc(e.what.join(' · ') || 'Suggestions at the time')}</div></div>
            <button class="btn btn-sm" data-mc-unignore>Stop Ignoring</button>
          </li>`).join('');
        el.innerHTML = `
          <div class="mc-run"><button class="btn" data-mc-run>${p.ui.icon('refresh')} Run Now</button>
            <span class="mc-dim">Last check: ${esc(last)}</span></div>
          <h4 class="mc-head">Ignored suggestions</h4>
          ${rows ? `<ul class="mc-list">${rows}</ul>` : '<p class="mc-dim">None. Ignore one from an item’s menu (right-click) or the command palette.</p>'}`;
      }
      /** @param {MouseEvent} ev */
      async function onClick(ev) {
        const t = /** @type {HTMLElement} */ (ev.target);
        if (t.closest('[data-mc-run]')) { await runNow(); return; }
        const row = t.closest('[data-mc-unignore]') && t.closest('[data-id]');
        if (row) {
          await unignore([String(/** @type {HTMLElement} */ (row).dataset.id)]);
          await draw();
        }
      }
      el.addEventListener('click', onClick);
      const off = p.onChange((kinds) => { if (kinds.includes('fields')) draw(); });
      draw();
      return () => { gone = true; off(); el.removeEventListener('click', onClick); };
    },
  });
}
