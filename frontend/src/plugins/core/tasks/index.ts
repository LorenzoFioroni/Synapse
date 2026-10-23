// Tasks supplies its page, note integrations and commands while the core plugin runs.
import type { PluginContext } from '../../api.js';
import { emit, state } from '../../../store.js';
import { setTasksPage } from '../../../tasksPage.js';
import { noteHooksChanged } from '../../../views/notes/hooks.js';
import { initNoteTasks, teardownNoteTasks } from './noteTasks.js';
import { startTaskData, stopTaskData } from './taskData.js';
import { closeTasks, initTasks, openTasks, tasksChanged, tasksOpen, tasksPanelButton, tasksShortcut, teardownTasks } from './tasksView.js';

export function activate(p: PluginContext): void {
  startTaskData();
  initTasks();
  initNoteTasks();
  setTasksPage({ open: openTasks, close: closeTasks, isOpen: tasksOpen, panelButton: tasksPanelButton, shortcut: tasksShortcut, changed: tasksChanged });
  noteHooksChanged('tasks');
  p.commands.register({
    id: 'toggle', title: 'Show Tasks', key: 'Mod-Shift-k', when: () => state.settings.notes_enabled !== false,
    run: () => { if (tasksOpen()) emit('close-notes'); else emit('open-tasks', {}); },
  });
  p.onUnload(() => {
    teardownNoteTasks(); teardownTasks(); stopTaskData(); setTasksPage(null); noteHooksChanged('tasks');
  });
}
