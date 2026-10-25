// Export the library or a selection through the running plugins' text writers.
import { api } from '../api.js';
import { state } from '../store.js';
import { openDialog } from '../ui/dialog.js';
import { toast, toastError } from '../ui/toast.js';
import type { Format } from '../types.js';
import { copyText, downloadBlob, escapeHtml, plural } from '../util.js';


/** Export ``ids`` (or, with null, the whole library). */
export async function openExport(ids: string[] | null = null): Promise<void> {
  let formats: Format[];
  try { formats = (await api.formats()).exports; } catch (e) { toastError(e); return; }
  if (!formats.length) { toast('No export formats are on. Turn on Export Formats in Settings → Plugins.'); return; }
  let fmt = formats.some((f) => f.key === state.settings.export_format) ? state.settings.export_format : formats[0].key;
  let request = 0;
  let ready = false;
  let lastText = '';
  const scope = ids && ids.length ? plural(ids.length, 'selected item') : `the whole library (${plural(state.counts.library, 'item')})`;
  await openDialog({
    title: 'Export',
    subtitle: `Export ${escapeHtml(scope)}.`,
    wide: true,
    render: (body) => {
      body.innerHTML = `<label class="field-label">Format</label>
        <div class="format-grid">${formats.map((f) => `<button class="format-opt ${f.key === fmt ? 'active' : ''}" data-fmt="${f.key}">
          <div class="n">${escapeHtml(f.label)}</div><div class="e">.${f.extension}</div></button>`).join('')}</div>
        <label class="field-label" style="margin-top:16px">Preview</label>
        <pre class="export-preview"><span class="muted">Loading…</span></pre>
        <div class="field-hint">Options such as BibTeX encoding and abstracts are in Settings → Import &amp; Export.</div>`;
      const pre = body.querySelector('pre')!;
      const refresh = async () => {
        const current = ++request;
        ready = false; lastText = '';
        pre.innerHTML = '<span class="muted">Loading…</span>';
        try {
          const res = await api.exportText(fmt, ids);
          if (current !== request) return;
          ready = true; lastText = res.text;
          const lines = res.text.split('\n');
          pre.textContent = lines.slice(0, 80).join('\n') + (lines.length > 80 ? `\n… (${lines.length - 80} more lines)` : '');
          if (!res.text.trim()) pre.innerHTML = '<span class="muted">Nothing to export.</span>';
        } catch (e) { if (current === request) pre.textContent = (e as Error).message; }
      };
      body.querySelector('.format-grid')!.addEventListener('click', (e) => {
        const b = (e.target as Element).closest<HTMLElement>('[data-fmt]');
        if (!b) return;
        fmt = b.dataset.fmt!;
        body.querySelectorAll('.format-opt').forEach((x) => x.classList.toggle('active', x === b));
        refresh();
      });
      refresh();
    },
    buttons: [
      { label: 'Cancel', value: null },
      { label: 'Copy to Clipboard', onClick: async () => { if (!ready) { toast('Wait for the export preview to finish.'); return false; } await copyText(lastText); toast('Copied to clipboard', { type: 'success' }); return false; } },
      {
        label: 'Download', kind: 'primary',
        onClick: async () => {
          try {
            const res = await fetch(api.exportUrl(fmt, ids));
            if (!res.ok) throw new Error(`Export failed (${res.status})`);
            const name = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '')?.[1] || `export.${fmt}`;
            downloadBlob(await res.blob(), name);
            if (fmt !== state.settings.export_format) {
              api.saveSettings({ export_format: fmt }).then((r) => { state.settings = r.settings; }).catch(() => {});
            }
          } catch (e) { toastError(e); return false; }
          return undefined;
        },
      },
    ],
  });
}
