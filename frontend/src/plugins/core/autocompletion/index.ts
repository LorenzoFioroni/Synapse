// Autocompletion, a core plugin (plugins/autocompletion): ghost-text suggestions in the notes
// editor (source.ts gives them to the editor through views/notes/hooks.ts) and its section in
// Settings (settings.ts). The model runs in Ollama on this computer; the plugin's server part
// talks to it.
import type { PluginContext } from '../../api.js';
import { noteHooks, setNoteHook } from '../../../views/notes/hooks.js';
import { makeSource } from './source.js';
import { mountSettings } from './settings.js';

export function activate(p: PluginContext): void {
  const src = makeSource(p);
  setNoteHook('ghost', src.ghost);
  const opened = () => { src.check(); };   // load the model, so the first suggestion does not wait for it
  noteHooks.editorOpened.add(opened);
  src.check();
  p.settingsPage.register({ id: 'ollama', title: 'Autocompletion', icon: 'sparkle', after: 'general', mount: (el) => mountSettings(p, el, src) });
  p.onUnload(() => {
    if (noteHooks.ghost === src.ghost) setNoteHook('ghost', null);
    noteHooks.editorOpened.delete(opened);
  });
}
