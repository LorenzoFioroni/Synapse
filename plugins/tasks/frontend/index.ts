// Tasks owns its UI; records, history and note-link integrity stay behind the public service API.
import type { PluginContext } from '../../../frontend/src/plugins/api.js';
import { emit, off, on } from '../../../frontend/src/plugins/task-runtime.js';
import { configureApi } from './api.js';
import { taskHooks, startEnvironment } from './environment.js';
import { renderDefaults } from './rendering.js';
import { initNoteTasks, taskKey, teardownNoteTasks } from './noteTasks.js';
import { startTaskData, stopTaskData } from './taskData.js';
import { closeTasks, initTasks, openTasks, tasksChanged, tasksOpen, tasksPanelButton, tasksShortcut, teardownTasks } from './tasksView.js';
import { dueCount, mountPhoneTasks, quickTask } from './phoneView.js';
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

  // the phone layout: a page of its own, "Task" in the + sheet, and a button above the keyboard
  p.pages.register({ id: 'tasks', title: 'Tasks', icon: 'tasks', phone: (el, nav) => mountPhoneTasks(el, nav) });
  p.capture.register({ id: 'task', title: 'Task', icon: 'tasks', run: () => quickTask(p.ui.prompt) });
  p.editor.toolbar.register({ id: 'task', title: 'Make this line a task', icon: 'tasks', run: (view) => taskKey(view) });
  const badge = () => p.pages.setBadge('tasks', dueCount() || null);
  on('tasks', badge);
  p.onUnload(() => off('tasks', badge));
}
