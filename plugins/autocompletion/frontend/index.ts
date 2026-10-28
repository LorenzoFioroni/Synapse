// Suggestions and settings use only the public plugin context.
import type { PluginContext } from '../../../frontend/src/plugins/api.js';
import { makeSource } from './source.js';
import { mountSettings } from './settings.js';

export function activate(p: PluginContext): void {
  const src = makeSource(p);
  p.editor.suggestions.register(src.ghost);
  p.editor.onOpened(() => { void src.check(); });
  void src.check();
  p.settingsPage.register({ id: 'ollama', title: 'Autocompletion', icon: 'sparkle', shows: ['model', 'length', 'url'], mount: (el) => mountSettings(p, el, src) });
}
