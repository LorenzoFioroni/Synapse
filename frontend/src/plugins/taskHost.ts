// The host keeps native Tasks navigation and note integration while the contributed UI runs.
import { navigation } from '../extend.js';
import { on, off, emit, type AppEvents } from '../store.js';
import { setTasksPage } from '../tasksPage.js';
import { taskHooks, notesEnabled } from '../views/notes/workspace.js';
import { noteHooksChanged } from '../views/notes/hooks.js';
import { renderDefaults } from '../views/notes/render.js';
import type { TaskIntegration } from './task-runtime.js';
let active: TaskIntegration | null = null;
export function registerTaskIntegration(owner: string, integration: TaskIntegration): () => void {
  if (active) throw new Error('A Tasks interface is already registered');
  active = integration;
  const before = { taskHtml: renderDefaults.taskHtml, taskBlockHtml: renderDefaults.taskBlockHtml };
  Object.assign(taskHooks, integration.notes);
  Object.assign(renderDefaults, integration.render);
  setTasksPage(integration.page);
  let counts: AppEvents['tasks-count'] = null;
  const changed = (value: AppEvents['tasks-count']) => { counts = value; navigation.changed(); };
  on('tasks-count', changed);
  const remove = navigation.register({
    id: `${owner}.main`, title: 'Tasks', icon: 'tasks', when: notesEnabled,
    active: integration.page.isOpen, open: () => emit('open-tasks', {}),
    badge: () => counts?.open ? { text: String(counts.open), warning: counts.overdue ? String(counts.overdue) : undefined,
      warningTitle: `${counts.overdue} overdue` } : null,
  });
  noteHooksChanged('tasks');
  return () => {
    if (active !== integration) return;
    active = null;
    off('tasks-count', changed); remove();
    integration.destroy();
    for (const key of Object.keys(taskHooks) as (keyof typeof taskHooks)[]) taskHooks[key] = null;
    Object.assign(renderDefaults, before);
    setTasksPage(null); noteHooksChanged('tasks');
  };
}
