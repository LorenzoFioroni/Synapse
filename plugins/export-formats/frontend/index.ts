// Optional note export alongside the plugin's existing reference writers.
import type { PluginContext } from '../../../frontend/src/plugins/api.js';
import {
  citationIndex, createRenderer, downloadBlob, equationMode, exportBibliography, exportNoteFile,
  exportByline, exportMathSettings, loadMD, noteIndex, noteSection, openDialog, splitFrontmatter, storage, store,
} from '../../../frontend/src/plugins/task-runtime.js';
import { imageSrc } from '../../../frontend/src/plugins/markdown.js';
import { latexProject, noteResolver } from './latex.js';
import { zipFiles } from './zip.js';

const COLUMNS_KEY = 'zc.export.latexGalleryColumns';

async function localImage(source: string): Promise<{ bytes: Uint8Array; extension: string } | null> {
  const url = imageSrc(source);
  // Export stays offline and never sends authenticated requests to note-supplied hosts.
  if (!url.startsWith('/api/notes/attachments/') && !/^data:image\/(png|jpeg|gif|webp);base64,/i.test(url)) return null;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Could not load figure: ${source}`);
  const blob = await response.blob();
  const extension = ({ 'image/png': 'png', 'image/jpeg': 'jpg' } as Record<string, string>)[blob.type];
  if (extension) return { bytes: new Uint8Array(await blob.arrayBuffer()), extension };
  // LaTeX cannot include GIF/WebP: rasterize with the browser's offline decoder.
  if (!['image/gif', 'image/webp'].includes(blob.type)) return null;
  const bitmap = await createImageBitmap(blob);
  try {
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width; canvas.height = bitmap.height;
    canvas.getContext('2d')!.drawImage(bitmap, 0, 0);
    const png = await new Promise<Blob>((resolve, reject) => canvas.toBlob(b => b ? resolve(b) : reject(new Error('Could not convert figure.')), 'image/png'));
    return { bytes: new Uint8Array(await png.arrayBuffer()), extension: 'png' };
  } finally { bitmap.close(); }
}

export function activate(p: PluginContext): void {
  let stopped = false;
  let closeDialog: (() => void) | null = null;
  p.onUnload(() => { stopped = true; closeDialog?.(); });
  p.notes.exports.register({
    id: 'latex', title: 'LaTeX project…', icon: 'doc',
    async run(note) {
      let linked = false;
      // remembered on this device: a choice of layout, not of content
      let columns: number = [1, 2, 3, 4].find(n => n === storage<number>(COLUMNS_KEY, 0)) ?? 0;
      const accepted = await openDialog({
        title: 'Export LaTeX',
        subtitle: 'Download a ZIP with main.tex, figures and references.bib, for pdfLaTeX.',
        render(body, close) {
          closeDialog = () => close(null);
          body.innerHTML = '<label class="checkbox"><input type="checkbox" name="linked-notes"> Include linked notes</label>'
            + '<p class="field-hint">Includes all reachable linked and embedded notes as separate files in one project. With this off, embedded notes are included in main.tex.</p>'
            + '<label class="field-label" for="latex-gallery-columns" style="margin-top:16px">Gallery images per row</label>'
            + `<select class="select" id="latex-gallery-columns" name="gallery-columns">${[[0, 'Automatic (up to three)'], [1, 'One'], [2, 'Two'], [3, 'Three'], [4, 'Four']]
              .map(([value, text]) => `<option value="${value}"${value === columns ? ' selected' : ''}>${text}</option>`).join('')}</select>`
            + '<p class="field-hint">A gallery becomes one figure, its panels marked (a), (b), … and their captions added to the figure\'s.</p>';
          body.querySelector<HTMLInputElement>('input[name="linked-notes"]')!.addEventListener('change', e => { linked = (e.target as HTMLInputElement).checked; });
          body.querySelector<HTMLSelectElement>('select')!.addEventListener('change', e => { columns = Number((e.target as HTMLSelectElement).value) || 0; });
        },
        buttons: [{ label: 'Cancel', value: false }, { label: 'Download', kind: 'primary', value: true }],
      });
      closeDialog = null;
      if (!accepted || stopped) return;
      store(COLUMNS_KEY, columns);
      const [lib, listing, citations] = await Promise.all([loadMD(), noteIndex(), citationIndex()]);
      const renderer = createRenderer(lib);
      // Keep raw equation labels, references and macro definitions for TeX rather than KaTeX.
      renderer.md.core.ruler.disable('equations');
      const byKey = new Map(citations.items.filter(item => !item.trashed).map(item => [item.key, item]));
      const project = await latexProject(note, linked, {
        parse: text => renderer.md.parse(text, {}),
        body: text => text.slice(splitFrontmatter(text).bodyStart),
        section: noteSection,
        resolve: noteResolver([...new Set([note.path, ...listing.notes.map(n => n.path)])]),
        read: async path => {
          const file = await exportNoteFile(path);
          return { path, text: file.text, title: file.info?.title || path.replace(/\.md$/i, '').split('/').pop()! };
        },
        image: localImage,
        math: { ...exportMathSettings(), numbering: equationMode() },
        galleryColumns: columns,
        header: exportByline(),
        bibliography: async keys => {
          const ids = [...new Set(keys.map(key => byKey.get(key)?.id).filter((id): id is string => !!id))];
          const result = ids.length ? await exportBibliography(ids) : { text: '' };
          return { text: result.text, missing: keys.filter(key => !byKey.has(key)) };
        },
      });
      if (stopped) return;
      const bytes = zipFiles(project.files);
      const name = note.title.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, ' ').trim().slice(0, 120) || 'note';
      downloadBlob(new Blob([bytes as Uint8Array<ArrayBuffer>], { type: 'application/zip' }), `${name}.zip`);
      if (project.warnings.length) p.notify('LaTeX project exported. See README.txt for unavailable content.', { type: 'info', transient: true });
    },
  });
}
