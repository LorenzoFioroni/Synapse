// Graph, a core plugin (plugins/graph): the graph of notes and the papers they cite. The notes
// view shows it through views/notes/hooks.ts (a local graph beside the note, the full graph in
// place of the editor); this adds the command (Cmd/Ctrl+Shift+G) and Show in Graph for items.
import type { PluginContext } from '../../api.js';
import { state } from '../../../store.js';
import { noteHooks, setNoteHook, type GraphHook } from '../../../views/notes/hooks.js';
import { closeGlobal, globalOpen, graphChanged, initNotesGraph, localSection, openGlobal, showLocal, teardownNotesGraph } from './notesGraph.js';

/** Leave Settings, Tasks or a plugin's page for the graph in Notes. */
async function leaveOthers(): Promise<void> {
  const [{ closeSettings, settingsOpen }, { closeTasks }, { closePage }] = await Promise.all([
    import('../../../views/settingsView.js'), import('../../../views/tasks/tasksView.js'), import('../../../views/pages.js'),
  ]);
  if (settingsOpen()) closeSettings();
  closeTasks();
  closePage();
}

export function activate(p: PluginContext): void {
  const hook: GraphHook = {
    attach: initNotesGraph, localSection, showLocal, changed: graphChanged, openGlobal, closeGlobal, globalOpen,
  };
  setNoteHook('graph', hook);
  const notesOn = () => state.settings.notes_enabled !== false;
  p.commands.register({
    id: 'toggle', title: 'Show the Graph', key: 'Mod-Shift-g', when: notesOn,
    run: async () => { await leaveOthers(); (await import('../../../views/notes/notesView.js')).toggleNotesGraph(); },
  });
  p.itemActions.register({
    id: 'show', title: 'Show in Graph', icon: 'link',
    when: (items) => items.length === 1 && notesOn(),
    run: async (items) => { await leaveOthers(); (await import('../../../views/notes/notesView.js')).openNotesGraph(`item:${items[0].id}`); },
  });
  p.onUnload(() => {
    if (noteHooks.graph === hook) setNoteHook('graph', null);
    teardownNotesGraph();
  });
}
