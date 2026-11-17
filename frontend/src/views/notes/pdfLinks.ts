// A [[folder/file.pdf#p=4]] link in a note: finding the PDF in the notes folders and opening it in
// the viewer at the page.
import { openNotePdf } from '../../api.js';
import { type CiteTarget, viewerTarget } from './citeTarget.js';
import type { NotePdf } from '../../types.js';
import { pdfPage } from './mdscan.js';
import { ws } from './workspace.js';

/** The PDF a link target (or a path the backend resolved) names. */
export const findPdf = (target: string): NotePdf | undefined => {
  const t = target.trim().replace(/\\/g, '/').toLowerCase();
  return (ws.overview.pdfs || []).find((p) => p.path.toLowerCase() === t)
    || (ws.overview.pdfs || []).find((p) => p.path.toLowerCase().endsWith(`/${t}`) || p.name.toLowerCase() === t);
};

/** Open the PDF ``path`` at the page ``heading`` names (and, for a link ending a quote, at the quoted
 *  words: ``spot``); false when it is not in the listing. */
export async function openPdfLink(path: string, heading = '', { newTab = false, spot = null }: { newTab?: boolean; spot?: CiteTarget | null } = {}): Promise<boolean> {
  const pdf = findPdf(path);
  if (!pdf) return false;
  const at = await viewerTarget(spot).catch(() => ({} as { q?: string }));
  openNotePdf(pdf, pdfPage(heading), { q: at.q }, { newTab });
  return true;
}
