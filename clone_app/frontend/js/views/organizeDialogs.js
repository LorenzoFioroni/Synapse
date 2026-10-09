// Dialogs and widgets for folders, tags and smart folders.
import * as A from '../actions.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { folderById, folderPath, folderTree, selectedItems, state } from '../store.js';
import { openDialog, promptDialog } from '../ui/dialog.js';
import { toastError } from '../ui/toast.js';
import { debounce, escapeHtml, h, plural, shortAuthors } from '../util.js';

/* ------------------------------------------------------------ tag input */
/**
 * Chip input with autocomplete. Calls onAdd(name) / onRemove(name).
 * Returns { set(tags), focus() }.
 */
export function tagInput(container, { tags = [], onAdd, onRemove, placeholder = 'Add tag…', autofocus = false }) {
  container.classList.add('tag-input', 'no-enter');   // Enter adds a tag instead of submitting the dialog
  container.innerHTML = '<div class="chips"></div><input type="text" spellcheck="false" autocomplete="off"><div class="tag-suggest" hidden></div>';
  const chips = container.querySelector('.chips');
  const input = container.querySelector('input');
  const box = container.querySelector('.tag-suggest');
  input.placeholder = placeholder;
  let current = [...tags];
  let active = -1;

  const paint = () => {
    chips.innerHTML = current.map((t) => tagChip(t, true)).join('');
  };
  const suggestions = () => {
    const q = input.value.trim().toLowerCase();
    const have = new Set(current.map((t) => t.toLowerCase()));
    return state.sidebar.tags
      .filter((t) => !have.has(t.name.toLowerCase()) && (!q || t.name.toLowerCase().includes(q)))
      .sort((a, b) => (b.name.toLowerCase().startsWith(q) - a.name.toLowerCase().startsWith(q)) || b.count - a.count)
      .slice(0, 8);
  };
  const showSuggest = () => {
    const list = suggestions();
    const q = input.value.trim();
    const exact = state.sidebar.tags.some((t) => t.name.toLowerCase() === q.toLowerCase());
    const rows = list.map((t) => ({ name: t.name, label: `${tagDot(t.name)}<span>${escapeHtml(t.name)}</span><span class="n">${t.count}</span>` }));
    if (q && !exact && !current.some((t) => t.toLowerCase() === q.toLowerCase())) rows.push({ name: q, label: `${icon('plus', 'icon-sm')}<span>Create “${escapeHtml(q)}”</span>` });
    active = rows.length ? 0 : -1;
    box.innerHTML = rows.map((r, i) => `<div class="opt ${i === active ? 'active' : ''}" data-name="${escapeHtml(r.name)}">${r.label}</div>`).join('');
    box.hidden = !rows.length || document.activeElement !== input;
  };
  const add = (name) => {
    name = name.trim().replace(/\s+/g, ' ');
    if (!name || current.some((t) => t.toLowerCase() === name.toLowerCase())) { input.value = ''; box.hidden = true; return; }
    const existing = state.sidebar.tags.find((t) => t.name.toLowerCase() === name.toLowerCase());
    name = existing ? existing.name : name;
    current.push(name);
    paint();
    input.value = '';
    onAdd?.(name);
    box.hidden = true;   // reopen when the user types again
  };
  input.addEventListener('input', showSuggest);
  input.addEventListener('focus', showSuggest);
  input.addEventListener('blur', () => setTimeout(() => { box.hidden = true; }, 150));
  input.addEventListener('keydown', (e) => {
    const opts = [...box.querySelectorAll('.opt')];
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!opts.length) return;
      e.preventDefault();
      active = (active + (e.key === 'ArrowDown' ? 1 : -1) + opts.length) % opts.length;
      opts.forEach((o, i) => o.classList.toggle('active', i === active));
    } else if (e.key === 'Enter' || e.key === ',' || e.key === 'Tab') {
      if (e.key === 'Tab' && !input.value) return;
      e.preventDefault();
      e.stopPropagation();
      if (!box.hidden && opts[active] && (e.key !== ',' || !input.value.trim())) add(opts[active].dataset.name);
      else add(input.value);
    } else if (e.key === 'Backspace' && !input.value && current.length) {
      const last = current.pop();
      paint();
      onRemove?.(last);
    } else if (e.key === 'Escape' && !box.hidden) {
      e.stopPropagation();
      box.hidden = true;
    }
  });
  box.addEventListener('mousedown', (e) => {
    const o = e.target.closest('.opt');
    if (o) { e.preventDefault(); add(o.dataset.name); }
  });
  chips.addEventListener('click', (e) => {
    const x = e.target.closest('[data-remove]');
    if (!x) { input.focus(); return; }
    const name = x.dataset.remove;
    current = current.filter((t) => t !== name);
    paint();
    onRemove?.(name);
  });
  container.addEventListener('click', (e) => { if (e.target === container) input.focus(); });
  paint();
  if (autofocus) setTimeout(() => input.focus(), 40);
  return { set(t) { current = [...t]; paint(); }, focus: () => input.focus(), value: () => current };
}

export function tagDot(name) {
  const c = state.sidebar.tags.find((t) => t.name.toLowerCase() === String(name).toLowerCase())?.color;
  return `<span class="tag-dot" style="${c ? `background:${c}` : ''}"></span>`;
}

export function tagChip(name, removable = false) {
  return `<span class="tag-chip">${tagDot(name)}<span>${escapeHtml(name)}</span>${removable
    ? `<button class="x" data-remove="${escapeHtml(name)}" title="Remove tag" tabindex="-1">${icon('close', 'icon-sm')}</button>` : ''}</span>`;
}

/* --------------------------------------------------------- folder picker */
/** Resolves to a folder id, null (= Unfiled) or undefined (cancelled). */
export function pickFolder({ title = 'Move to Folder', confirm = 'Move', current = undefined, allowNone = true } = {}) {
  let chosen = current === undefined ? null : current;
  let list, filter;
  const render = () => {
    const q = filter.value.trim().toLowerCase();
    const rows = folderTree().filter((f) => !q || folderPath(f.id).join('/').toLowerCase().includes(q));
    list.innerHTML = (allowNone && !q ? `<div class="fp-row ${chosen === null ? 'active' : ''}" data-id="">${icon('library', 'icon-sm')}<span>Unfiled (no folder)</span></div>` : '')
      + rows.map((f) => `<div class="fp-row ${chosen === f.id ? 'active' : ''}" data-id="${f.id}" style="padding-left:${10 + (q ? 0 : f.depth * 18)}px">
          ${icon('folder', 'icon-sm')}<span>${escapeHtml(q ? folderPath(f.id).join(' / ') : f.name)}</span><span class="n">${f.count || ''}</span></div>`).join('')
      + (!rows.length && q ? '<div class="fp-empty">No folder matches.</div>' : '')
      + (!state.sidebar.folders.length ? '<div class="fp-empty">No folders yet — create one below.</div>' : '');
  };
  return openDialog({
    title,
    render: (body, close) => {
      body.innerHTML = `<input class="input" placeholder="Filter folders…" spellcheck="false">
        <div class="fp-list"></div>
        <button class="btn btn-ghost btn-sm" data-new style="margin-top:8px">${icon('plus', 'icon-sm')} New Folder…</button>`;
      filter = body.querySelector('input');
      list = body.querySelector('.fp-list');
      render();
      filter.addEventListener('input', render);
      list.addEventListener('click', (e) => {
        const r = e.target.closest('.fp-row');
        if (!r) return;
        chosen = r.dataset.id ? Number(r.dataset.id) : null;
        render();
      });
      list.addEventListener('dblclick', (e) => { if (e.target.closest('.fp-row')) body.closest('.dialog').querySelector('.btn-primary').click(); });
      body.querySelector('[data-new]').onclick = async () => {
        const parent = chosen ? folderById(chosen) : null;
        const name = await promptDialog(parent ? `New folder inside “${parent.name}”` : 'New folder', { title: 'New Folder', confirm: 'Create', placeholder: 'Folder name' });
        if (!name) return;
        const f = await A.createFolder(name, parent ? parent.id : null, { open: false });
        if (f) { chosen = f.id; render(); }
      };
    },
    buttons: [{ label: 'Cancel', value: undefined }, { label: confirm, kind: 'primary', onClick: () => {} }],
  }).then((v) => (v === confirm ? chosen : undefined));
}

export async function moveSelectionToFolder(ids = selectedItems().map((i) => i.id)) {
  if (!ids.length) return;
  const items = ids.map((id) => state.items.find((i) => i.id === id)).filter(Boolean);
  const same = items.every((i) => i.folder_id === items[0].folder_id) ? items[0]?.folder_id ?? null : undefined;
  const fid = await pickFolder({ title: ids.length === 1 ? 'Move to Folder' : `Move ${ids.length} Items to Folder`, current: same });
  if (fid === undefined) return;
  await A.moveToFolder(ids, fid);
}

/* -------------------------------------------------------- edit tags (N) */
export function editTagsDialog(ids = selectedItems().map((i) => i.id)) {
  const items = ids.map((id) => state.items.find((i) => i.id === id)).filter(Boolean);
  const counts = {};
  items.forEach((i) => (i.tags || []).forEach((t) => { counts[t] = (counts[t] || 0) + 1; }));
  const toAdd = [], toRemove = new Set();
  return openDialog({
    title: items.length === 1 ? 'Edit Tags' : `Tags for ${items.length} Items`,
    render: (body) => {
      body.innerHTML = `<label class="field-label">Add tags</label><div class="ti"></div>
        <div class="field-hint">Press Enter or comma after each tag. New tags are created automatically.</div>
        ${Object.keys(counts).length ? `<label class="field-label" style="margin-top:16px">Current tags</label>
        <div class="current-tags">${Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([t, n]) => `
          <label class="ct-row"><input type="checkbox" checked data-tag="${escapeHtml(t)}">${tagChip(t)}
          <span class="subtle">${items.length > 1 ? `${n} of ${items.length}` : ''}</span></label>`).join('')}</div>
        <div class="field-hint">Untick a tag to remove it from ${items.length > 1 ? 'all selected items' : 'this item'}.</div>` : ''}`;
      tagInput(body.querySelector('.ti'), {
        autofocus: true,
        onAdd: (n) => { if (!toAdd.includes(n)) toAdd.push(n); },
        onRemove: (n) => { const i = toAdd.indexOf(n); if (i >= 0) toAdd.splice(i, 1); },
      });
      body.querySelectorAll('[data-tag]').forEach((cb) => cb.addEventListener('change', () => {
        cb.checked ? toRemove.delete(cb.dataset.tag) : toRemove.add(cb.dataset.tag);
      }));
    },
    buttons: [
      { label: 'Cancel', value: null },
      {
        label: 'Save', kind: 'primary',
        onClick: async () => {
          const pending = document.querySelector('.dialog .tag-input input')?.value.trim();
          if (pending) toAdd.push(pending);
          if (!toAdd.length && !toRemove.size) return undefined;
          try {
            await api.tagItems(ids, toAdd, [...toRemove]);
            await A.loadItems();
          } catch (e) { toastError(e); return false; }
          return undefined;
        },
      },
    ],
  });
}

/* --------------------------------------------------- smart folder editor */
const EXAMPLES = [
  ['tag:review', 'items tagged “review”'],
  ['year:>2020 has:pdf', 'recent items with a PDF'],
  ['folder:"Physics/Transport" -tag:read', 'unread items in a folder (and its subfolders)'],
  ['author:cirac type:journal', 'journal articles by an author'],
  ['lindblad year:2015-2020', 'text (full text with that scope) plus a year range'],
  ['tag:none folder:none', 'items not organised yet'],
];

export function smartFolderDialog(existing = null, preset = {}) {
  let name, query, mode, preview;
  const refresh = debounce(async () => {
    const q = query.value.trim();
    if (!q) { preview.innerHTML = '<span class="subtle">Type a query to see what matches.</span>'; return; }
    try {
      const res = await api.searchPreview(q, mode.value);
      preview.innerHTML = `<div class="sp-count"><b>${plural(res.count, 'item')}</b> match right now</div>`
        + res.sample.map((s) => `<div class="sp-row">${escapeHtml(s.title || 'Untitled')}<span class="subtle"> · ${escapeHtml([shortAuthors(s.authors), s.year].filter(Boolean).join(' · '))}</span></div>`).join('')
        + (res.count > res.sample.length ? `<div class="subtle sp-row">…and ${res.count - res.sample.length} more</div>` : '');
    } catch (e) { preview.innerHTML = `<span class="form-error">${escapeHtml(e.message)}</span>`; }
  }, 250);
  return openDialog({
    title: existing ? 'Edit Smart Folder' : 'New Smart Folder',
    subtitle: 'A smart folder is a saved search: it always shows the items that currently match.',
    wide: true,
    render: (body) => {
      const modes = state.settingsMeta.searchModes || { basic: 'Title, Abstract & Authors', all: 'All Fields', fulltext: 'All Fields & Full Text' };
      body.innerHTML = `
        <div class="sf-grid">
          <div><label class="field-label">Name</label><input class="input" data-name placeholder="e.g. To read"></div>
          <div><label class="field-label">Search text in</label><select class="select" data-mode>${Object.entries(modes).map(([k, l]) => `<option value="${k}">${escapeHtml(l)}</option>`).join('')}</select></div>
        </div>
        <label class="field-label" style="margin-top:14px">Query</label>
        <input class="input mono" data-query placeholder='tag:review year:>2020 -tag:read' spellcheck="false">
        <div class="sf-help"><div class="field-hint">Filters: <code>tag:</code> <code>folder:</code> <code>type:</code> <code>year:</code> <code>has:pdf</code> <code>author:</code> <code>title:</code> <code>journal:</code> — combine freely, prefix <code>-</code> to exclude, quote multi-word values.</div>
          <div class="sf-examples">${EXAMPLES.map(([q, d]) => `<button class="sf-ex" data-q="${escapeHtml(q)}"><code>${escapeHtml(q)}</code><span>${escapeHtml(d)}</span></button>`).join('')}</div></div>
        <label class="field-label" style="margin-top:14px">Preview</label>
        <div class="sf-preview"></div>`;
      name = body.querySelector('[data-name]');
      query = body.querySelector('[data-query]');
      mode = body.querySelector('[data-mode]');
      preview = body.querySelector('.sf-preview');
      name.value = existing?.name || preset.name || '';
      query.value = existing?.query || preset.query || '';
      mode.value = existing?.mode || preset.mode || 'all';
      query.addEventListener('input', refresh);
      mode.addEventListener('change', refresh);
      body.querySelector('.sf-examples').addEventListener('click', (e) => {
        const b = e.target.closest('[data-q]');
        if (!b) return;
        query.value = b.dataset.q;
        query.focus();
        refresh();
      });
      refresh();
      setTimeout(() => (name.value ? query : name).focus(), 50);
    },
    buttons: [
      { label: 'Cancel', value: null },
      {
        label: existing ? 'Save' : 'Create', kind: 'primary',
        onClick: async () => {
          const n = name.value.trim() || query.value.trim();
          try {
            const sf = existing
              ? await api.updateSmart(existing.id, { name: n, query: query.value, mode: mode.value })
              : await api.createSmart(n, query.value, mode.value);
            if (!existing) A.setView(`smart:${sf.id}`);
            await A.loadItems();
          } catch (e) { toastError(e); return false; }
          return undefined;
        },
      },
    ],
  });
}
