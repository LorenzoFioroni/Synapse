// "Add Item by Identifier": DOI, arXiv ID or ISBN (several at once, one per line).
import * as A from '../actions.js';
import { afterAddChoices, afterAddHtml, runAfterAdd } from '../afterAdd.js';
import { api } from '../api.js';
import { currentFolderId, folderById } from '../store.js';
import { openDialog } from '../ui/dialog.js';
import { toast } from '../ui/toast.js';
import { escapeHtml, plural } from '../util.js';

export function openIdentifierDialog() {
  let ta, status, after;
  return openDialog({
    title: 'Add Item by Identifier',
    render: (body) => {
      body.innerHTML = `<label class="field-label">DOI, arXiv ID or ISBN</label>
        <textarea class="input" rows="3" placeholder="10.1103/PhysRevLett.114.220601&#10;arXiv:2205.03415&#10;978-0-262-03384-8"></textarea>
        <div class="field-hint">Paste several identifiers, one per line, to add them all. <kbd>Enter</kbd> adds, <kbd>Shift</kbd>+<kbd>Enter</kbd> starts a new line.
          ${currentFolderId() ? `<br>New items go into the folder <b>${escapeHtml(folderById(currentFolderId())?.name || '')}</b>.` : ''}</div>
        <div class="id-after">${afterAddHtml(['pdf', 'references'], { pdf: 'Find an available PDF', references: 'Fetch the reference list' })}</div>
        <div class="id-status" style="margin-top:10px;display:flex;flex-direction:column;gap:4px"></div>`;
      after = body.querySelector('.id-after');
      ta = body.querySelector('textarea');
      status = body.querySelector('.id-status');
      ta.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); body.closest('.dialog').querySelector('.btn-primary').click(); }
      });
    },
    buttons: [
      { label: 'Cancel', value: null },
      {
        label: 'Add', kind: 'primary',
        onClick: async (close, btn) => {
          const ids = ta.value.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
          if (!ids.length) { ta.focus(); return false; }
          btn.disabled = true;
          status.innerHTML = '';
          const created = [];
          let failed = 0;
          const what = afterAddChoices(after, ['pdf', 'references']);
          const opts = { fetchReferences: what.references };   // the server only uses it for "ask"
          for (const ident of ids) {
            const line = document.createElement('div');
            line.className = 'muted';
            line.innerHTML = `<span class="spinner"></span> ${escapeHtml(ident)}`;
            status.appendChild(line);
            try {
              const item = await api.fromIdentifier(ident, false, currentFolderId(), opts);
              created.push(item.id);
              line.innerHTML = `✓ <b>${escapeHtml(item.title || ident)}</b>`;
            } catch (e) {
              if (e.status === 409) {
                line.innerHTML = `• Already in your library: ${escapeHtml(e.data?.metadata?.title || ident)} `;
                const add = document.createElement('button');
                add.className = 'btn btn-ghost btn-sm';
                add.textContent = 'Add anyway';
                add.onclick = async () => {
                  const item = await api.fromIdentifier(ident, true, currentFolderId(), opts);
                  add.remove(); line.innerHTML = `✓ <b>${escapeHtml(item.title)}</b>`;
                  await A.loadItems(); A.select([item.id], { anchor: item.id });
                  runAfterAdd([item.id], { pdf: what.pdf });
                };
                line.appendChild(add);
                A.select([e.data.duplicate_of], { anchor: e.data.duplicate_of });
              } else {
                failed++;
                line.innerHTML = `<span style="color:var(--danger)">✕ ${escapeHtml(ident)} — ${escapeHtml(e.message)}</span>`;
              }
            }
          }
          btn.disabled = false;
          if (created.length) {
            await A.loadItems(); A.select(created, { anchor: created[0] });
            toast(`Added ${plural(created.length, 'item')}`, { type: 'success' });
            runAfterAdd(created, { pdf: what.pdf });   // references: already queued by the server
          }
          if (!failed && created.length === ids.length) return undefined;  // close when everything worked
          ta.value = '';
          return false;
        },
      },
    ],
  });
}
