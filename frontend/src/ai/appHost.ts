// Ask AI inside the app: opening a cited PDF goes through the app's own tabs (api.ts).
import { openAttachment } from '../api.js';
import type { AskContext } from './client.js';
import type { AskHost } from './panel.js';

const host: AskHost = {
  openPdf: (item, pdf, page) => openAttachment(item, pdf, page),
  showItem: (id) => { location.hash = `item=${id}`; },
};

/** Open the panel (loaded on first use) for ``context``. */
export async function askAi(context?: AskContext): Promise<void> {
  const { openAskAi } = await import('./panel.js');
  openAskAi(context, host);
}
