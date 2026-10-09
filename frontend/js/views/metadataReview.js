// "Retrieve Metadata" for one or many items.
//
// Fresh metadata is fetched item by item (gently, cancellable) and shown in a
// single review dialog with one section per article: proposed changes as word
// diffs with a checkbox each, followed by all the item's other metadata.
// Nothing is saved until "Apply" is pressed.
import * as A from '../actions.js';
import { api } from '../api.js';
import { icon, typeIcon } from '../icons.js';
import { byId, state } from '../store.js';
import { openDialog } from '../ui/dialog.js';
import { toast, toastError } from '../ui/toast.js';
import { diffWords, escapeHtml, h, plural, shortAuthors } from '../util.js';

// Fields the lookup can return, in display order.
const LOOKUP_FIELDS = [
  'item_type', 'title', 'authors', 'year', 'journal', 'volume', 'issue', 'pages', 'publisher', 'place',
  'doi', 'url', 'issn', 'isbn', 'citation_key', 'short_title', 'language', 'license', 'archive', 'archive_id',
  'library_catalog', 'abstract', 'extra',
];
const MULTILINE = new Set(['title', 'authors', 'abstract', 'extra']);
const PAUSE_MS = 350;   // between requests, to be polite to Crossref/OpenAlex

const blank = (v) => v === null || v === undefined || String(v).trim() === '';

function label(key) {
  return state.schema.fields.find((f) => f.key === key)?.label || key;
}
function display(key, v) {
  if (key === 'item_type') return state.schema.itemTypes.find((t) => t.key === v)?.label || v || '';
  return v === null || v === undefined ? '' : String(v);
}

/** Changed fields between the current item and the fetched metadata. */
function computeChanges(current, fetched) {
  return LOOKUP_FIELDS
    .filter((k) => !blank(fetched[k]) && String(fetched[k]) !== String(current[k] ?? ''))
    .filter((k) => !(k === 'item_type' && blank(current[k])))
    .map((k) => ({
      key: k,
      current: current[k],
      fetched: fetched[k],
      isNew: blank(current[k]),
      // replacing an existing citation key would break documents that cite it: opt-in
      checked: !(k === 'citation_key' && !blank(current[k])),
    }));
}

/** Every non-empty field of the item that the lookup leaves as it is. */
function unchangedFields(current, changes) {
  const changed = new Set(changes.map((c) => c.key));
  return state.schema.fields
    .filter((f) => f.editable && !changed.has(f.key) && !blank(current[f.key]))
    .map((f) => ({ key: f.key, value: display(f.key, current[f.key]) }));
}

/* ------------------------------------------------------------------ entry */
export async function openMetadataReview(idOrIds) {
  const ids = (Array.isArray(idOrIds) ? idOrIds : [idOrIds]).filter((id) => byId(id));
  if (!ids.length) return;

  // A single item with nothing new doesn't need a dialog.
  let prefetched = null;
  if (ids.length === 1) {
    const close = toast('Looking up metadata…', { timeout: 0 });
    try { prefetched = await api.lookup(ids[0]); } catch (e) { close(); return toastError(e); }
    close();
    if (!computeChanges(prefetched.current, prefetched.fetched).length) {
      return toast('Metadata is already up to date', { type: 'success' });
    }
  }
  return reviewDialog(ids, prefetched);
}

/* ----------------------------------------------------------------- dialog */
function reviewDialog(ids, prefetched) {
  const articles = ids.map((id, n) => ({ id, n: n + 1, item: byId(id), status: 'pending', changes: [], unchanged: [], error: null }));
  let stopped = false, finished = false, applyBtn, list, summary, stopBtn;

  const refreshSummary = () => {
    const done = articles.filter((a) => ['changes', 'same', 'error'].includes(a.status)).length;
    const skipped = articles.filter((a) => a.status === 'skipped').length;
    const withChanges = articles.filter((a) => a.changes.length).length;
    const ticked = articles.reduce((n, a) => n + a.changes.filter((c) => c.checked).length, 0);
    const failed = articles.filter((a) => a.status === 'error').length;
    const running = !finished && !stopped;
    summary.innerHTML = `${running ? '<span class="spinner"></span>' : icon('check', 'icon-sm')}
      <span>${running ? `Looking up ${Math.min(done + 1, articles.length)} of ${articles.length}…` : `Looked up ${done} of ${plural(articles.length, 'article')}`}</span>
      <span class="mr-dot">·</span><span>${plural(withChanges, 'article')} with updates</span>
      ${failed ? `<span class="mr-dot">·</span><span class="mr-fail">${failed} not found</span>` : ''}
      ${skipped ? `<span class="mr-dot">·</span><span>stopped, ${skipped} not looked up</span>` : ''}
      <span class="spacer"></span><b>${plural(ticked, 'change')} selected</b>`;
    stopBtn.hidden = !running || articles.length === 1;
    applyBtn.disabled = ticked === 0 || running;
    applyBtn.textContent = ticked ? `Apply ${plural(ticked, 'Change')}` : 'Apply';
  };

  const run = async () => {
    for (const a of articles) {
      if (stopped || !list.isConnected) break;
      try {
        const res = prefetched && a.id === prefetched.current.id ? prefetched : await api.lookup(a.id);
        a.item = res.current;
        a.changes = computeChanges(res.current, res.fetched);
        a.unchanged = unchangedFields(res.current, a.changes);
        a.status = a.changes.length ? 'changes' : 'same';
      } catch (e) {
        a.status = 'error';
        a.error = e.message;
        a.unchanged = unchangedFields(a.item, []);
        if (e.status === 0) stopped = true;   // server unreachable
      }
      if (!list.isConnected) { stopped = true; return; }   // dialog was closed
      renderArticle(a);
      refreshSummary();
      if (!stopped && a !== articles[articles.length - 1] && !(prefetched && articles.length === 1)) {
        await new Promise((r) => setTimeout(r, PAUSE_MS));
      }
    }
    finished = true;
    articles.filter((a) => a.status === 'pending').forEach((a) => { a.status = 'skipped'; renderArticle(a); });
    refreshSummary();
  };

  const renderArticle = (a) => {
    const old = list.querySelector(`[data-article="${a.id}"]`);
    const el = articleSection(a, refreshSummary);
    old ? old.replaceWith(el) : list.appendChild(el);
  };

  return openDialog({
    title: ids.length === 1 ? 'Review Metadata Updates' : `Review Metadata for ${ids.length} Articles`,
    subtitle: 'Untick anything you want to keep as it is, or edit a value before applying. Nothing is saved until you apply.',
    wide: true,
    render: (body, close, buttons) => {
      applyBtn = buttons[buttons.length - 1];
      body.closest('.dialog').classList.add('mr-dialog');
      body.innerHTML = `<div class="mr-summary"></div><div class="mr-list"></div>`;
      summary = body.querySelector('.mr-summary');
      list = body.querySelector('.mr-list');
      stopBtn = h('<button class="btn btn-sm">Stop</button>');
      stopBtn.onclick = () => { stopped = true; stopBtn.disabled = true; stopBtn.textContent = 'Stopping…'; };
      body.closest('.dialog').querySelector('.dialog-footer .left').append(stopBtn);
      articles.forEach(renderArticle);
      refreshSummary();
      setTimeout(run, 0);   // start once the dialog is in the document
    },
    footerLeft: (() => {
      const b = h('<button class="btn btn-ghost btn-sm">Select all / none</button>');
      b.onclick = () => {
        const all = articles.flatMap((a) => a.changes);
        const on = !all.every((c) => c.checked);
        all.forEach((c) => { c.checked = on; });
        articles.forEach(renderArticle);
        refreshSummary();
      };
      return b;
    })(),
    buttons: [
      { label: 'Cancel', value: null, onClick: () => { stopped = true; } },
      {
        label: 'Apply', kind: 'primary',
        onClick: async (close, btn) => {
          btn.disabled = true;
          btn.textContent = 'Saving…';
          const ok = await applyChanges(articles);
          if (!ok) { btn.disabled = false; btn.textContent = 'Apply'; return false; }
          stopped = true;
          return undefined;
        },
      },
    ],
  });
}

/* --------------------------------------------------------- one article */
function articleSection(a, onChange) {
  const it = a.item || {};
  const sub = [shortAuthors(it.authors), it.year, it.journal].filter(Boolean).join(' · ');
  const ticked = a.changes.filter((c) => c.checked).length;
  const badge = {
    pending: '<span class="badge"><span class="spinner" style="width:10px;height:10px;border-width:1.5px"></span>&nbsp;Looking up…</span>',
    changes: `<span class="badge accent">${plural(a.changes.length, 'update')}</span>`,
    same: '<span class="badge ok">Up to date</span>',
    error: '<span class="badge err">Not found</span>',
    skipped: '<span class="badge">Not looked up</span>',
  }[a.status];
  const el = h(`<section class="mr-article ${a.status}" data-article="${a.id}">
      <header class="mr-head">
        <span class="mr-num">${a.n}</span>
        ${a.changes.length ? `<input type="checkbox" class="mr-all" title="Apply all updates for this article" ${ticked === a.changes.length ? 'checked' : ''}>` : `<span class="mr-type">${typeIcon(it.item_type, 'icon-sm')}</span>`}
        <div class="mr-titles"><div class="mr-title">${escapeHtml(it.title || 'Untitled')}</div>
          ${sub ? `<div class="mr-sub">${escapeHtml(sub)}</div>` : ''}</div>
        ${badge}
        <button class="icon-btn mr-collapse" title="Collapse">${icon('chevronDown', 'icon-sm')}</button>
      </header>
      <div class="mr-body"></div>
    </section>`);
  const all = el.querySelector('.mr-all');
  if (all) all.indeterminate = ticked > 0 && ticked < a.changes.length;
  const body = el.querySelector('.mr-body');

  if (a.status === 'error') body.appendChild(h(`<div class="mr-error">${icon('alert', 'icon-sm')} ${escapeHtml(a.error || 'Lookup failed')}</div>`));
  if (a.status === 'pending') body.appendChild(h('<div class="mr-pending">Waiting for the metadata service…</div>'));

  if (a.changes.length) {
    const box = h('<div class="mr-changes"><div class="mr-label">Proposed changes</div></div>');
    for (const c of a.changes) box.appendChild(changeBlock(c, () => { syncHeader(); onChange(); }));
    body.appendChild(box);
  }
  if (a.unchanged.length && a.status !== 'pending') {
    const grid = h(`<div class="mr-unchanged"><div class="mr-label">${a.changes.length ? 'Other metadata (unchanged)' : 'Current metadata'}</div>
      <dl>${a.unchanged.map((f) => `<dt>${escapeHtml(label(f.key))}</dt><dd class="${f.key === 'abstract' ? 'clamp' : ''}">${escapeHtml(f.value)}</dd>`).join('')}</dl></div>`);
    grid.querySelectorAll('dd.clamp').forEach((dd) => dd.addEventListener('click', () => dd.classList.toggle('open')));
    body.appendChild(grid);
  }

  function syncHeader() {
    const n = a.changes.filter((c) => c.checked).length;
    if (all) { all.checked = n === a.changes.length; all.indeterminate = n > 0 && n < a.changes.length; }
  }
  all?.addEventListener('change', () => {
    a.changes.forEach((c) => { c.checked = all.checked; });
    body.querySelectorAll('.diff-block input[type=checkbox]').forEach((cb) => { cb.checked = all.checked; cb.dispatchEvent(new Event('sync')); });
    onChange();
  });
  el.querySelector('.mr-collapse').addEventListener('click', () => el.classList.toggle('collapsed'));
  return el;
}

function changeBlock(c, onToggle) {
  const cur = display(c.key, c.current), nxt = display(c.key, c.fetched);
  const d = diffWords(cur, nxt);
  const note = c.key === 'citation_key' && !c.isNew ? '<span class="subtle" style="font-weight:400;font-size:var(--fs-xs)">notes are updated; LaTeX \\cite commands are not</span>' : '';
  const block = h(`<div class="diff-block ${c.checked ? '' : 'off'}">
      <div class="diff-head"><label class="checkbox"><input type="checkbox" ${c.checked ? 'checked' : ''}> <b>${escapeHtml(label(c.key))}</b>
        ${c.isNew ? '<span class="badge accent">new</span>' : ''} ${note}</label>
        ${c.key === 'item_type' ? '' : '<button class="btn btn-ghost btn-sm" data-edit>Edit</button>'}</div>
      <div class="diff-body">${c.isNew ? '' : `<div class="diff-old">${d.old || '<em>(empty)</em>'}</div>`}<div class="diff-new">${d.new || '<em>(empty)</em>'}</div></div>
      <div class="diff-edit" hidden><textarea class="input" rows="${c.key === 'abstract' ? 6 : (MULTILINE.has(c.key) ? 3 : 1)}"></textarea></div>
    </div>`);
  const cb = block.querySelector('input');
  const ta = block.querySelector('textarea');
  ta.value = String(c.fetched);
  const sync = () => { c.checked = cb.checked; block.classList.toggle('off', !cb.checked); };
  cb.addEventListener('change', () => { sync(); onToggle(); });
  cb.addEventListener('sync', sync);
  ta.addEventListener('input', () => { c.fetched = ta.value; });
  block.querySelector('[data-edit]')?.addEventListener('click', (e) => {
    const edit = block.querySelector('.diff-edit');
    const opening = edit.hidden;
    edit.hidden = !opening;
    block.querySelector('.diff-body').hidden = opening;
    e.target.textContent = opening ? 'Show Diff' : 'Edit';
    if (opening) ta.focus();
    else {
      const nd = diffWords(cur, ta.value);
      const o = block.querySelector('.diff-old'); if (o) o.innerHTML = nd.old || '<em>(empty)</em>';
      block.querySelector('.diff-new').innerHTML = nd.new || '<em>(empty)</em>';
    }
  });
  return block;
}

/* ----------------------------------------------------------------- apply */
async function applyChanges(articles) {
  // Keys proposed for several articles in this batch must stay unique.
  const others = new Set(state.items.filter((i) => !articles.some((a) => a.id === i.id)).map((i) => i.citation_key).filter(Boolean));
  const uniqueKey = (k) => {
    let key = k, n = 0;
    while (others.has(key)) key = k + String.fromCharCode(97 + n++);
    others.add(key);
    return key;
  };
  let saved = 0, fields = 0;
  const errors = [];
  for (const a of articles) {
    const updates = {};
    for (const c of a.changes) if (c.checked) updates[c.key] = c.fetched;
    if (updates.citation_key) updates.citation_key = uniqueKey(updates.citation_key);
    else if (a.item?.citation_key) others.add(a.item.citation_key);
    if (!Object.keys(updates).length) continue;
    try {
      const item = await api.updateItem(a.id, updates);
      A.patchLocal(item);
      saved++;
      fields += Object.keys(updates).length;
    } catch (e) { errors.push(`${a.item?.title || a.id}: ${e.message}`); }
  }
  await A.loadItems();
  // the reference lists come with the metadata (in the background; see Settings → Online Services)
  const looked = articles.filter((a) => a.status === 'changes' || a.status === 'same').map((a) => a.id);
  if (looked.length && state.settings.references_auto !== false) api.queueReferences(looked).catch(() => {});
  if (errors.length) {
    toastError(new Error(`${errors.length} could not be saved — ${errors[0]}`));
    return saved > 0;
  }
  toast(saved === 1 ? `Updated ${plural(fields, 'field')}` : `Updated ${plural(fields, 'field')} in ${plural(saved, 'article')}`, { type: 'success' });
  return true;
}
