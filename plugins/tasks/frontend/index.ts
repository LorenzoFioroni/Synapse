// Tasks owns its UI; records, history and note-link integrity stay behind the public service API.
import type { PluginContext } from '../../../frontend/src/plugins/api.js';
import { emit } from '../../../frontend/src/plugins/task-runtime.js';
import { configureApi } from './api.js';
import { taskHooks, startEnvironment } from './environment.js';
import { renderDefaults } from './rendering.js';
import { initNoteTasks, teardownNoteTasks } from './noteTasks.js';
import { startTaskData, stopTaskData } from './taskData.js';
import { closeTasks, initTasks, openTasks, tasksChanged, tasksOpen, tasksPanelButton, tasksShortcut, teardownTasks } from './tasksView.js';
export function activate(p: PluginContext): void {
  configureApi(p);
  startEnvironment(p);
  startTaskData(); initTasks(); initNoteTasks();
  p.tasks.register({
    page: { open: openTasks, close: closeTasks, isOpen: tasksOpen, panelButton: tasksPanelButton, shortcut: tasksShortcut, changed: tasksChanged },
    notes: taskHooks, render: renderDefaults,
    destroy() { teardownNoteTasks(); teardownTasks(); stopTaskData(); },
  });
  p.commands.register({ id: 'toggle', title: 'Show Tasks', key: 'Mod-Shift-k', when: p.notes.enabled,
    run: () => { if (tasksOpen()) emit('close-notes'); else emit('open-tasks', {}); } });
}
