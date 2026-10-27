// The frontends of core plugins: plugins that come with Synapse in plugins/<id>/ with
// "core": true and "frontend": "core:<name>" in their manifest. None does at the moment (every
// bundled plugin uses the public API); the mechanism stays for features that need it. They are
// part of the app's own build (so they share its modules: the store, the dialogs, the notes
// editor), and the plugin host starts and stops them like any plugin's frontend (plugins/host.ts).
import type { PluginModule } from '../api.js';

export const CORE: Record<string, () => Promise<Partial<PluginModule>>> = {
};
