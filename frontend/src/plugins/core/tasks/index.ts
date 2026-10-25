// Tasks supplies its page, note integrations and commands while the core plugin runs.
import type { PluginContext } from '../../api.js';
import { navigation } from '../../../extend.js';
import { type AppEvents, emit, off, on, state } from '../../../store.js';
import { setTasksPage } from '../../../tasksPage.js';
import { noteHooksChanged } from '../../../views/notes/hooks.js';
import { initNoteTasks, teardownNoteTasks } from './noteTasks.js';
import { startTaskData, stopTaskData } from './taskData.js';
import { closeTasks, initTasks, openTasks, tasksChanged, tasksOpen, tasksPanelButton, tasksShortcut, teardownTasks } from './tasksView.js';

export function activate(p: PluginContext): void {
  let counts: AppEvents['tasks-count'] = null;
  const countChanged = (value: AppEvents['tasks-count']) => { counts = value; navigation.changed(); };
  on('tasks-count', countChanged);
  const removeNavigation = navigation.register({
    id: 'tasks.main', title: 'Tasks', icon: 'tasks', when: () => state.settings.notes_enabled !== false,
    active: tasksOpen, open: () => emit('open-tasks', {}),
    badge: () => counts?.open ? {
      text: String(counts.open), warning: counts.overdue ? String(counts.overdue) : undefined,
      warningTitle: `${counts.overdue} overdue`,
    } : null,
  });
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
    off('tasks-count', countChanged); removeNavigation();
    teardownNoteTasks(); teardownTasks(); stopTaskData(); setTasksPage(null); noteHooksChanged('tasks');
  });
}
