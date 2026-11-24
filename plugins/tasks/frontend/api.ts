import type { PluginContext } from '../../../frontend/src/plugins/api.js';
import type { Task, TaskChanges, TaskRef, TaskHistoryEntry, TasksOverview } from '../../../frontend/src/plugins/task-runtime.js';
import { noteIndex, citationIndex } from '../../../frontend/src/plugins/task-runtime.js';
let plugin: PluginContext;
export function configureApi(p: PluginContext): void { plugin = p; }
function call<T = unknown>(method: 'GET' | 'POST' | 'PATCH', path: string, body?: unknown, offline?: () => T): Promise<T> {
  if (method === 'GET') return plugin.backend.get<T>(path);
  const options = offline ? { offline } : undefined;
  return method === 'PATCH' ? plugin.backend.patch<T>(path, body, options) : plugin.backend.post<T>(path, body, options);
}
/** Offline use (plugin API 1.16): edits and new tasks wait on the device while Synapse cannot be reached. */
export const offline = {
  now: () => plugin.offline.now(),
  pending: () => plugin.offline.pending(),
  onSynced: (fn: () => void) => plugin.offline.onSynced(fn),
};
export const api = {
  tasks: () => call<TasksOverview>('GET', '/'),
  getTask: (id: string) => call<{ task: Task & { refs: TaskRef[]; subtasks: Task[] } }>('GET', `/${id}`),
  createTask: (data: TaskChanges & { id?: string }, offline?: () => { task: Task; ref?: { path: string; line: number } }) =>
    call<{ task: Task; ref?: { path: string; line: number } }>('POST', '/', data, offline),
  updateTask: (id: string, data: TaskChanges & { base?: Partial<Task> }, offline?: () => { task: Task; changed: Task[] }) =>
    call<{ task: Task; changed: Task[] }>('PATCH', `/${id}`, data, offline),
  deleteTasks: (ids: string[], batch?: string, offline?: () => { batch: string; ids: string[] }) =>
    call<{ batch: string; ids: string[] }>('POST', '/delete', batch ? { ids, batch } : { ids }, offline),
  restoreTasks: (batch: string, offline?: () => { ids: string[] }) => call<{ ids: string[] }>('POST', '/restore', { batch }, offline),
  restoreTask: (id: string, offline?: () => { ids: string[] }) => call<{ ids: string[] }>('POST', '/restore', { id }, offline),
  taskTrash: () => call<{ tasks: Task[] }>('GET', '/trash'),
  addTaskToNote: (id: string, path: string, afterLine: number | null = null, offline?: () => { ref: { path: string; line: number } }) =>
    call<{ ref: { path: string; line: number } }>('POST', `/${id}/notes`, { path, after_line: afterLine }, offline),
  removeTaskFromNote: (id: string, path: string, offline?: () => { path: string; removed: number }) =>
    call<{ path: string; removed: number }>('POST', `/${id}/notes/remove`, { path }, offline),
  convertToTasks: (text: string) => call<{ text: string; tasks: Task[] }>('POST', '/convert', { text }),
  taskHistory: (id: string) => call<{ history: TaskHistoryEntry[] }>('GET', `/${id}/history`),
  undoTaskChange: (historyId: number) => call<{ task: Task }>('POST', '/undo', { history_id: historyId }),
  notes: noteIndex, citeIndex: citationIndex,
  uploadNoteImage(file: File) { const body = new FormData(); body.append('file', file); return plugin.backend.post<{ path: string; markdown: string }>('/images', body); },
};
