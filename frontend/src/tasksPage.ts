// The Tasks plugin supplies its page while it runs; other workspaces use this optional interface.
import { emit } from './store.js';

export interface TasksPage {
  open(o: { task?: string | null; note?: string | null }): Promise<void>;
  close(): void;
  isOpen(): boolean;
  panelButton(action: string): void;
  shortcut(e: KeyboardEvent, key: string, mod: boolean): boolean;
  changed(): void;
}
let page: TasksPage | null = null;
export const tasksPage = {
  get on(): boolean { return page !== null; },
  open: (o: { task?: string | null; note?: string | null } = {}): Promise<void> => page?.open(o) ?? Promise.resolve(),
  close: (): void => { page?.close(); },
  isOpen: (): boolean => page?.isOpen() ?? false,
  panelButton: (action: string): void => { page?.panelButton(action); },
  shortcut: (e: KeyboardEvent, key: string, mod: boolean): boolean => page?.shortcut(e, key, mod) ?? false,
  changed: (): void => { page?.changed(); },
};
export function setTasksPage(value: TasksPage | null): void {
  page = value;
  emit('tasks-page');
}
