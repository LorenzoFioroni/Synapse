// Ask AI inside the app: opening a cited PDF goes through the app's own tabs (api.ts).
import { openAttachment } from '../api.js';
import type { AskContext } from './client.js';
import type { Dock } from './placement.js';
import type { AskHost } from './panel.js';

/** Docked, the panel takes the place of the note's details panel (css/notes.css, .ai-docked). */
const notesDock: Dock = {
  available: () => !!document.getElementById('notes-view') && matchMedia('(min-width: 1101px)').matches,
  mount(el) {
    const view = document.getElementById('notes-view')!;
    view.classList.add('ai-docked');
    view.querySelector('.notes-side')!.append(el);
  },
  unmount() { document.getElementById('notes-view')?.classList.remove('ai-docked'); },
};

const host: AskHost = {
  dock: notesDock,
  openPdf: (item, pdf, page) => openAttachment(item, pdf, page),
  showItem: (id) => { location.hash = `item=${id}`; },
};

/** Open the panel (loaded on first use) for ``context``. */
export async function askAi(context?: AskContext): Promise<void> {
  const { openAskAi } = await import('./panel.js');
  openAskAi(context, host);
}
