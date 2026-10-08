// "Retrieve Metadata": fetch fresh metadata and let the user review a word
// diff per field before anything is saved.
import * as A from '../actions.js';
import { api } from '../api.js';
import { state } from '../store.js';
import { openDialog } from '../ui/dialog.js';
import { toast, toastError } from '../ui/toast.js';
import { diffWords, escapeHtml, h, plural } from '../util.js';

const FIELDS = [
  ['item_type', 'Item Type'], ['title', 'Title'], ['authors', 'Authors'], ['year', 'Year'], ['journal', 'Publication'],
  ['volume', 'Volume'], ['issue', 'Issue'], ['pages', 'Pages'], ['publisher', 'Publisher'], ['doi', 'DOI'],
  ['url', 'URL'], ['issn', 'ISSN'], ['isbn', 'ISBN'], ['citation_key', 'Citation Key'], ['short_title', 'Short Title'],
  ['language', 'Language'], ['license', 'License'], ['archive', 'Archive'], ['archive_id', 'Archive ID'],
  ['library_catalog', 'Library Catalog'], ['abstract', 'Abstract'], ['extra', 'Extra'],
];

export async function openMetadataReview(id) {
  const close = toast('Looking up metadata…', { timeout: 0 });
  let res;
  try {
    res = await api.lookup(id);
  } catch (e) {
    close();
    return toastError(e);
  }
  close();
  const { current, fetched } = res;
  const changes = FIELDS.filter(([k]) => fetched[k] !== undefined && fetched[k] !== null && String(fetched[k]) !== String(current[k] ?? ''))
    .filter(([k]) => !(k === 'item_type' && !current[k]));
  if (!changes.length) return toast('Metadata is already up to date', { type: 'success' });

  const label = (k, v) => (k === 'item_type' ? (state.schema.itemTypes.find((t) => t.key === v)?.label || v) : v);
  const blocks = [];
  await openDialog({
    title: 'Review Metadata Updates',
    subtitle: `${plural(changes.length, 'field')} would change. Untick what you want to keep, or edit a value before applying.`,
    wide: true,
    render: (body) => {
      body.innerHTML = '<div class="diff-list"></div>';
      const list = body.firstElementChild;
      for (const [k, name] of changes) {
        const cur = String(label(k, current[k] ?? '') ?? '');
        const nxt = String(label(k, fetched[k]) ?? '');
        const d = diffWords(cur, nxt);
        const empty = current[k] === null || current[k] === undefined || current[k] === '';
        const block = h(`<div class="diff-block">
          <div class="diff-head"><label class="checkbox"><input type="checkbox" checked> <b>${escapeHtml(name)}</b>
            ${empty ? '<span class="badge accent">new</span>' : ''}</label>
            ${k === 'item_type' ? '' : '<button class="btn btn-ghost btn-sm" data-edit>Edit</button>'}</div>
          <div class="diff-body">${empty ? '' : `<div class="diff-old">${d.old || '<em>(empty)</em>'}</div>`}<div class="diff-new">${d.new || '<em>(empty)</em>'}</div></div>
          <div class="diff-edit" hidden><textarea class="input" rows="${k === 'abstract' ? 6 : (k === 'title' || k === 'authors' ? 3 : 1)}"></textarea></div></div>`);
        const cb = block.querySelector('input');
        const ta = block.querySelector('textarea');
        ta.value = String(fetched[k]);
        cb.addEventListener('change', () => block.classList.toggle('off', !cb.checked));
        block.querySelector('[data-edit]')?.addEventListener('click', (e) => {
          const editing = block.querySelector('.diff-edit').hidden;
          block.querySelector('.diff-edit').hidden = !editing;
          block.querySelector('.diff-body').hidden = editing;
          e.target.textContent = editing ? 'Show Diff' : 'Edit';
          if (!editing) {
            const nd = diffWords(cur, ta.value);
            const o = block.querySelector('.diff-old'); if (o) o.innerHTML = nd.old || '<em>(empty)</em>';
            block.querySelector('.diff-new').innerHTML = nd.new || '<em>(empty)</em>';
          } else ta.focus();
        });
        blocks.push({ k, cb, ta });
        list.appendChild(block);
      }
    },
    footerLeft: (() => {
      const b = h('<button class="btn btn-ghost btn-sm">Toggle all</button>');
      b.onclick = () => { const on = !blocks.every((x) => x.cb.checked); blocks.forEach((x) => { x.cb.checked = on; x.cb.dispatchEvent(new Event('change')); }); };
      return b;
    })(),
    buttons: [
      { label: 'Cancel', value: null },
      {
        label: 'Apply Selected', kind: 'primary',
        onClick: async (close, btn) => {
          const updates = {};
          for (const b of blocks) if (b.cb.checked) updates[b.k] = b.ta.value;
          if (!Object.keys(updates).length) { close(null); return false; }
          btn.disabled = true; btn.textContent = 'Saving…';
          try {
            const item = await api.updateItem(id, updates);
            A.patchLocal(item);
            await A.loadItems();
            toast(`Updated ${plural(Object.keys(updates).length, 'field')}`, { type: 'success' });
          } catch (e) { toastError(e); btn.disabled = false; btn.textContent = 'Apply Selected'; return false; }
          return undefined;
        },
      },
    ],
  });
}
