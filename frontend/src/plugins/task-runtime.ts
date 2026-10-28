// Public task/editor SDK. These functions share the host's engines and event/history identity;
// plugins own their data cache, widgets, views and lifecycle instead of importing app modules.
export { icon } from '../icons.js';
export { escapeHtml, aiName, aiOwner, formatDate, h, modKey, modLabel, plural, storage, store } from '../util.js';
export { openDialog, promptDialog } from '../ui/dialog.js';
export { openMenu } from '../ui/menu.js';
export { toast, toastError } from '../ui/toast.js';
export { createEditor, loadMD } from '../views/notes/editor.js';
export type { NoteEditor } from '../views/notes/editor.js';
export { createRenderer } from '../views/notes/render.js';
export type { Renderer, TaskBlockOptions } from '../views/notes/render.js';
export type { PortableTask } from '../views/notes/portable.js';
export type { TaskChoice } from '../views/notes/completion.js';
export { renderMermaid } from '../views/notes/richRender.js';
export { scan } from '../views/notes/mdscan.js';
export { citeInfo, equationMode, noteMacros, normTarget, notesEnabled } from '../views/notes/workspace.js';
export { openNewWindow } from '../windows.js';
export { navPush, navReplace, navUpdate } from '../nav.js';
export { emit, on, off } from '../store.js';
export type { AppEvents, AppEvent } from '../store.js';
export type { Task, TaskChanges, TaskRef, NoteInfo, NotesListing, CiteEntry, TaskHistoryEntry, TasksOverview } from '../types.js';
import { ws, taskHooks } from '../views/notes/workspace.js';
import { state } from '../store.js';
import { api } from '../api.js';
import { renderDefaults } from '../views/notes/render.js';
import type { TasksPage } from '../tasksPage.js';
export type TaskNoteHooks = typeof taskHooks;
export interface TaskIntegration {
  page: TasksPage;
  notes: TaskNoteHooks;
  render: Pick<typeof renderDefaults, 'taskHtml' | 'taskBlockHtml'>;
  destroy(): void;
}
export const currentEditor = () => ws.editor;
export const currentNote = () => ws.session ? { path: ws.session.path, text: ws.session.text } : null;
export const libraryView = () => state.view;
export const librarySelection = () => new Set(state.selection);
export const noteIndex = () => api.notes();
export const citationIndex = () => api.citeIndex();
export const fallbackTaskHtml = renderDefaults.taskHtml;
export const fallbackTaskBlockHtml = renderDefaults.taskBlockHtml;

export const noteIndexSnapshot = () => structuredClone(ws.overview);
