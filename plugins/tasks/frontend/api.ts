import type { PluginContext } from '../../../frontend/src/plugins/api.js';
import type { Task, TaskChanges, TaskRef, TaskHistoryEntry, TasksOverview } from '../../../frontend/src/plugins/task-runtime.js';
import { noteIndex, citationIndex } from '../../../frontend/src/plugins/task-runtime.js';
let plugin: PluginContext;
export function configureApi(p: PluginContext): void { plugin = p; }
function call<T = unknown>(method: 'GET' | 'POST' | 'PATCH', path: string, body?: unknown): Promise<T> {
  if (method === 'GET') return plugin.backend.get<T>(path);
  return method === 'PATCH' ? plugin.backend.patch<T>(path, body) : plugin.backend.post<T>(path, body);
}
export const api = {
  tasks: () => call<TasksOverview>('GET', '/'),
  getTask: (id: string) => call<{ task: Task & { refs: TaskRef[]; subtasks: Task[] } }>('GET', `/${id}`),
  createTask: (data: TaskChanges) => call<{ task: Task; ref?: { path: string; line: number } }>('POST', '/', data),
  updateTask: (id: string, data: TaskChanges) => call<{ task: Task; changed: Task[] }>('PATCH', `/${id}`, data),
  deleteTasks: (ids: string[]) => call<{ batch: string; ids: string[] }>('POST', '/delete', { ids }),
  restoreTasks: (batch: string) => call<{ ids: string[] }>('POST', '/restore', { batch }),
  restoreTask: (id: string) => call<{ ids: string[] }>('POST', '/restore', { id }),
  taskTrash: () => call<{ tasks: Task[] }>('GET', '/trash'),
  addTaskToNote: (id: string, path: string, afterLine: number | null = null) => call<{ ref: { path: string; line: number } }>('POST', `/${id}/notes`, { path, after_line: afterLine }),
  removeTaskFromNote: (id: string, path: string) => call<{ path: string; removed: number }>('POST', `/${id}/notes/remove`, { path }),
  convertToTasks: (text: string) => call<{ text: string; tasks: Task[] }>('POST', '/convert', { text }),
  taskHistory: (id: string) => call<{ history: TaskHistoryEntry[] }>('GET', `/${id}/history`),
  undoTaskChange: (historyId: number) => call<{ task: Task }>('POST', '/undo', { history_id: historyId }),
  notes: noteIndex, citeIndex: citationIndex,
  uploadNoteImage(file: File) { const body = new FormData(); body.append('file', file); return plugin.backend.post<{ path: string; markdown: string }>('/images', body); },
};
