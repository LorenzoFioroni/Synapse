// Import references: pick/drop/paste a file, preview entries, flag duplicates.
import * as A from '../actions.js';
import { afterAddChoices, afterAddHtml, runAfterAdd } from '../afterAdd.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { currentFolderId, folderPath, folderTree, state } from '../store.js';
import { openDialog } from '../ui/dialog.js';
import { toast, toastError } from '../ui/toast.js';
import type { ImportEntry, ImportPreview } from '../types.js';
import { escapeHtml, plural, shortAuthors } from '../util.js';
import type { AfterAddKind } from '../afterAdd.js';

export async function openImport(initialFile: File | null = null): Promise<void> {
  let preview: ImportPreview | null = null;
  let entriesState: { e: ImportEntry; on: boolean }[] = [];
  let importBtn: HTMLButtonElement, folderSelect: HTMLSelectElement, afterBox: HTMLElement;
  const AFTER: AfterAddKind[] = ['metadata', 'pdf', 'references'];
  await openDialog({
    title: 'Import References',
    subtitle: 'BibTeX (.bib), RIS (.ris), EndNote (.enw or XML) and CSL JSON are detected automatically.',
    wide: true,
    render: (body, close, buttons) => {
      importBtn = buttons[buttons.length - 1];
      importBtn.disabled = true;
      body.innerHTML = `
        <div class="drop-zone" tabindex="0">${icon('import')}<div><b>Choose a file</b> or drop it here</div>
          <div class="subtle" style="font-size:var(--fs-xs);margin-top:4px">.bib · .ris · .enw · .xml · .json</div></div>
        <details style="margin-top:12px"><summary class="muted" style="cursor:pointer">…or paste text</summary>
          <textarea class="input no-enter" rows="6" placeholder="@article{key, title = {…}, …}" style="margin-top:8px;font-family:var(--font-mono);font-size:12px"></textarea>
          <button class="btn btn-sm" data-parse style="margin-top:6px">Preview pasted text</button></details>
        <div class="import-result"></div>
        <div class="import-folder"><label class="field-label" style="margin:14px 0 6px">Import into</label>
          <select class="select" data-folder style="max-width:360px"><option value="">My Library (no folder)</option>
          ${folderTree().map((f) => `<option value="${f.id}" ${f.id === currentFolderId() ? 'selected' : ''}>${'\u2003'.repeat(f.depth)}${escapeHtml(f.name)}</option>`).join('')}</select>
          <div class="import-after"></div></div>`;
      folderSelect = body.querySelector<HTMLSelectElement>('[data-folder]')!;
      afterBox = body.querySelector<HTMLElement>('.import-after')!;
      const dz = body.querySelector<HTMLElement>('.drop-zone')!;
      const result = body.querySelector<HTMLElement>('.import-result')!;
      const load = async (src: string | File) => {
        result.innerHTML = '<div class="import-summary"><span class="spinner"></span> Reading…</div>';
        try {
          preview = await api.importPreview(src);
          entriesState = preview!.entries.map((e) => ({ e, on: !(e.duplicate_of && state.settings.import_skip_duplicates !== false) }));
          showPreview(result);
          dz.hidden = true;
        } catch (e) {
          result.innerHTML = `<div class="form-error">${escapeHtml((e as Error).message)}</div>`;
          importBtn.disabled = true;
        }
      };
      const pick = () => {
        const input = document.createElement('input');
        input.type = 'file'; input.accept = '.bib,.bibtex,.ris,.enw,.xml,.json,.txt,.nbib';
        input.onchange = () => input.files?.[0] && load(input.files[0]);
        input.click();
      };
      dz.addEventListener('click', pick);
      dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
      dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('over'); });
      dz.addEventListener('dragleave', () => dz.classList.remove('over'));
      dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('over'); if (e.dataTransfer?.files[0]) load(e.dataTransfer.files[0]); });
      body.querySelector('[data-parse]')!.addEventListener('click', () => {
        const t = body.querySelector('textarea')!.value;
        if (t.trim()) load(t);
      });
      if (initialFile) load(initialFile);
    },
    buttons: [
      { label: 'Cancel', value: null },
      {
        label: 'Import', kind: 'primary',
        onClick: async (close, btn) => {
          const chosen = entriesState.filter((x) => x.on).map((x) => { const { duplicate_of: _dup, ...rest } = x.e; return rest; });
          if (!chosen.length) return false;
          btn.disabled = true; btn.textContent = 'Importing…';
          try {
            const folder = folderSelect.value ? Number(folderSelect.value) : null;
            const what = afterAddChoices(afterBox, ['tags', ...AFTER]);
            const res = await api.importCommit(chosen, false, folder, { keywordsAsTags: what.tags });
            A.setView(folder ? `folder:${folder}` : 'library');
            await A.loadItems();
            A.select(res.created, { anchor: res.created[0] });
            toast(`Imported ${plural(res.created.length, 'item')}`, { type: 'success' });
            runAfterAdd(res.created, what);
          } catch (e) { toastError(e); btn.disabled = false; btn.textContent = 'Import'; return false; }
          return undefined;
        },
      },
    ],
  });

  function showPreview(result: HTMLElement) {
    const dupes = entriesState.filter((x) => x.e.duplicate_of).length;
    // "Turn keywords into tags" only matters when the file has keywords
    const keywords = new Set(entriesState.flatMap((x) => (x.e.keywords || '').split(/[;,]/).map((k) => k.trim()).filter(Boolean)));
    afterBox.innerHTML = afterAddHtml(keywords.size ? ['tags', ...AFTER] : AFTER, {
      tags: `Turn the file's keywords into tags (${plural(keywords.size, 'keyword')})`,
      metadata: 'Retrieve metadata online, then review the changes',
      pdf: 'Find available PDFs',
      references: 'Fetch reference lists',
    });
    if (afterBox.innerHTML) afterBox.insertAdjacentHTML('afterbegin', '<label class="field-label" style="margin:14px 0 6px">After importing</label>');
    result.innerHTML = `
      <div class="import-summary">
        <span class="badge accent">${escapeHtml(preview!.label)}</span>
        <b>${plural(entriesState.length, 'reference')}</b>
        ${dupes ? `<span class="badge warn">${dupes} already in library</span>` : ''}
        <span class="spacer"></span>
        <label class="checkbox"><input type="checkbox" data-all ${entriesState.every((x) => x.on) ? 'checked' : ''}> Select all</label>
      </div>
      <div class="import-list">${entriesState.map((x, i) => `
        <label class="import-row"><input type="checkbox" data-i="${i}" ${x.on ? 'checked' : ''}>
          <div><div class="t">${escapeHtml(x.e.title || 'Untitled')}</div>
          <div class="m">${escapeHtml([shortAuthors(x.e.authors), x.e.year, x.e.journal].filter(Boolean).join(' · '))}${x.e.citation_key ? ` · <span class="mono">${escapeHtml(x.e.citation_key)}</span>` : ''}</div></div>
          ${x.e.duplicate_of ? '<span class="badge warn" title="An item with the same DOI or title is already in your library">duplicate</span>' : '<span></span>'}
        </label>`).join('')}</div>`;
    const update = () => {
      const n = entriesState.filter((x) => x.on).length;
      importBtn.disabled = n === 0;
      importBtn.textContent = n ? `Import ${plural(n, 'item')}` : 'Import';
    };
    result.addEventListener('change', (e) => {
      const box = e.target as HTMLInputElement;
      if (box.dataset.all !== undefined) {
        entriesState.forEach((x) => { x.on = box.checked; });
        result.querySelectorAll<HTMLInputElement>('[data-i]').forEach((cb) => { cb.checked = box.checked; });
      } else if (box.dataset.i !== undefined) entriesState[+box.dataset.i].on = box.checked;
      update();
    });
    update();
  }
}
