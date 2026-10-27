// Graph uses only the public plugin context; its data and rendering stay in this package.
import type { PluginContext } from '../../../frontend/src/plugins/api.js';
import { configureGraphLibrary } from './forceGraph.js';
import { configureUI } from './ui.js';
import { configure, closeGlobal, globalOpen, graphChanged, initNotesGraph, localSection, openGlobal, showLocal, teardownNotesGraph } from './notesGraph.js';

export function activate(p: PluginContext): void {
  configureUI(p);
  configure(p);
  configureGraphLibrary(p.assets.vendor('force-graph'));
  p.notes.visualizations.register({
    id: 'main', title: 'Graph', icon: 'link', attach: initNotesGraph, section: localSection,
    showNote: showLocal, changed: graphChanged, open: openGlobal,
    close: options => closeGlobal({ push: options?.notify }), isOpen: globalOpen, destroy: teardownNotesGraph,
  });
  p.commands.register({ id: 'toggle', title: 'Show the Graph', key: 'Mod-Shift-g', when: p.notes.enabled,
    run: () => p.notes.visualizations.toggle('main') });
  p.itemActions.register({ id: 'show', title: 'Show in Graph', icon: 'link',
    when: items => items.length === 1 && p.notes.enabled(),
    run: items => p.notes.visualizations.open('main', `item:${items[0].id}`) });
  p.onChange(kinds => { if (kinds.some(k => k === 'library' || k === 'notes')) graphChanged(); });
}
