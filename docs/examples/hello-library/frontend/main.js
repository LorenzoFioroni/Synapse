// Copy frontend/src/plugins/api.ts beside this file for editor type checking.
/** @param {import('./api').PluginContext} p */
export function activate(p) {
  p.pages.register({
    id: 'main', title: 'Hello Library', icon: 'plugin',
    mount(el) {
      const message = document.createElement('p');
      const refresh = document.createElement('button');
      refresh.className = 'btn';
      refresh.textContent = 'Refresh';
      el.append(message, refresh);
      let alive = true;
      let request = 0;
      async function draw() {
        const current = ++request;
        try {
          const result = /** @type {{message: string}} */ (await p.backend.get('/message'));
          if (alive && current === request) message.textContent = result.message;
        } catch (error) {
          if (!alive || current !== request || /** @type {Error} */ (error).name === 'AbortError') return;
          message.textContent = /** @type {Error} */ (error).message;
        }
      }
      refresh.addEventListener('click', draw);
      const off = p.onChange((kinds) => { if (kinds.includes('plugins')) draw(); });
      draw();
      return () => {
        alive = false;
        off();
        refresh.removeEventListener('click', draw);
      };
    },
  });
  p.commands.register({ id: 'open', title: 'Open Greeting', key: 'Mod-Alt-y', run: () => p.pages.open('main') });
}
