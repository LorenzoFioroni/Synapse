// Import references: pick/drop/paste a file, preview entries, flag duplicates.
import * as A from '../actions.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { state } from '../store.js';
import { openDialog } from '../ui/dialog.js';
import { toast, toastError } from '../ui/toast.js';
import { escapeHtml, plural, shortAuthors } from '../util.js';

export async function openImport(initialFile = null) {
  let preview = null;
  let entriesState = [];
  let importBtn;
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
        <div class="import-result"></div>`;
      const dz = body.querySelector('.drop-zone');
      const result = body.querySelector('.import-result');
      const load = async (src) => {
        result.innerHTML = '<div class="import-summary"><span class="spinner"></span> Reading…</div>';
        try {
          preview = await api.importPreview(src);
          entriesState = preview.entries.map((e) => ({ e, on: !(e.duplicate_of && state.settings.import_skip_duplicates !== false) }));
          showPreview(result);
          dz.hidden = true;
        } catch (e) {
          result.innerHTML = `<div class="form-error">${escapeHtml(e.message)}</div>`;
          importBtn.disabled = true;
        }
      };
      const pick = () => {
        const input = document.createElement('input');
        input.type = 'file'; input.accept = '.bib,.bibtex,.ris,.enw,.xml,.json,.txt,.nbib';
        input.onchange = () => input.files[0] && load(input.files[0]);
        input.click();
      };
      dz.addEventListener('click', pick);
      dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
      dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('over'); });
      dz.addEventListener('dragleave', () => dz.classList.remove('over'));
      dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('over'); if (e.dataTransfer.files[0]) load(e.dataTransfer.files[0]); });
      body.querySelector('[data-parse]').addEventListener('click', () => {
        const t = body.querySelector('textarea').value;
        if (t.trim()) load(t);
      });
      if (initialFile) load(initialFile);
    },
    buttons: [
      { label: 'Cancel', value: null },
      {
        label: 'Import', kind: 'primary',
        onClick: async (close, btn) => {
          const chosen = entriesState.filter((x) => x.on).map((x) => { const { duplicate_of, ...rest } = x.e; return rest; });
          if (!chosen.length) return false;
          btn.disabled = true; btn.textContent = 'Importing…';
          try {
            const res = await api.importCommit(chosen, false);
            if (state.view !== 'library') A.setView('library');
            await A.loadItems();
            A.select(res.created, { anchor: res.created[0] });
            toast(`Imported ${plural(res.created.length, 'item')}`, { type: 'success' });
          } catch (e) { toastError(e); btn.disabled = false; btn.textContent = 'Import'; return false; }
          return undefined;
        },
      },
    ],
  });

  function showPreview(result) {
    const dupes = entriesState.filter((x) => x.e.duplicate_of).length;
    result.innerHTML = `
      <div class="import-summary">
        <span class="badge accent">${escapeHtml(preview.label)}</span>
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
      if (e.target.dataset.all !== undefined) {
        entriesState.forEach((x) => { x.on = e.target.checked; });
        result.querySelectorAll('[data-i]').forEach((cb) => { cb.checked = e.target.checked; });
      } else if (e.target.dataset.i !== undefined) entriesState[+e.target.dataset.i].on = e.target.checked;
      update();
    });
    update();
  }
}
