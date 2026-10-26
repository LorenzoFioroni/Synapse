// The frontends of the core plugins: the plugins that come with Synapse in plugins/<id>/ with
// "core": true and "frontend": "core:<name>" in their manifest. They are part of the app's own
// build (so they share its modules: the store, the dialogs, the notes editor), and the plugin host
// starts and stops them like any plugin's frontend (plugins/host.ts).
import type { PluginModule } from '../api.js';

export const CORE: Record<string, () => Promise<Partial<PluginModule>>> = {
  autocompletion: () => import('./autocompletion/index.js'),
  'extended-markdown': () => import('./extended-markdown/index.js'),
  graph: () => import('./graph/index.js'),
  'metadata-check': () => import('./metadata-check/index.js'),
  tasks: () => import('./tasks/index.js'),
};
