// "Retrieve Metadata" for one or many items.
//
// Fresh metadata is fetched by a lookup job: several items at a time, cancellable, and able
// to keep running in the background (a progress toast) while you work. The results are shown
// in a single review dialog: articles with proposed changes first (word diffs with a checkbox
// each, followed by the item's other metadata), then the ones that were not found, and the
// ones that are already up to date folded away at the end. Nothing is saved until "Apply".
import * as A from '../actions.js';
import { api } from '../api.js';
import { icon, typeIcon } from '../icons.js';
import { byId, state } from '../store.js';
import { openDialog } from '../ui/dialog.js';
import { progressToast, toast, toastError } from '../ui/toast.js';
import { diffWords, escapeHtml, h, plural, shortAuthors } from '../util.js';

// Fields the lookup can return, in display order.
const LOOKUP_FIELDS = [
  'item_type', 'title', 'authors', 'year', 'journal', 'volume', 'issue', 'pages', 'publisher', 'place',
  'doi', 'url', 'issn', 'isbn', 'citation_key', 'short_title', 'language', 'license', 'archive', 'archive_id',
  'library_catalog', 'abstract', 'extra',
];
const MULTILINE = new Set(['title', 'authors', 'abstract', 'extra']);
// Lookups in flight at once. Crossref and OpenAlex handle this easily (with a contact e-mail
// you are in Crossref's "polite" pool); arXiv requests are spaced out by the server.
const CONCURRENCY = 4;
const PAUSE_MS = 120;   // per worker, between its requests

const blank = (v) => v === null || v === undefined || String(v).trim() === '';
const DONE = new Set(['changes', 'same', 'error']);

function label(key) {
  return state.schema.fields.find((f) => f.key === key)?.label || key;
}
function display(key, v) {
  if (key === 'item_type') return state.schema.itemTypes.find((t) => t.key === v)?.label || v || '';
  return v === null || v === undefined ? '' : String(v);
}

/** Changed fields between the current item and the fetched metadata. */
export function computeChanges(current, fetched) {
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

/* -------------------------------------------------------------------- job */
/**
 * Looks up metadata for a list of items, CONCURRENCY at a time, in order.
 * `lookup` is injectable for tests. Listeners get (article) after each result and
 * (null) when the job ends.
 */
export class LookupJob {
  constructor(ids, { prefetched = null, references = false, lookup = (id) => api.lookup(id), items = byId } = {}) {
    this.articles = ids.map((id, n) => ({ id, n: n + 1, item: items(id), status: 'pending', changes: [], unchanged: [], error: null }));
    this.prefetched = prefetched;
    this.references = references;   // also fetch reference lists of the articles when applying
    this.lookup = lookup;
    this.stopped = false;
    this.finished = false;
    this.applied = false;
    this.listeners = new Set();
    this.done = null;
  }

  get running() { return !this.finished; }
  count(status) { return this.articles.filter((a) => a.status === status).length; }
  get looked() { return this.articles.filter((a) => DONE.has(a.status)).length; }
  get withChanges() { return this.count('changes'); }

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  emit(a) { for (const fn of [...this.listeners]) { try { fn(a); } catch (e) { console.error(e); } } }

  start() {
    if (this.done) return this.done;
    let next = 0;
    const worker = async () => {
      while (!this.stopped && next < this.articles.length) {
        const a = this.articles[next++];
        await this.lookupOne(a);
        this.emit(a);
        if (!this.stopped && next < this.articles.length) await new Promise((r) => setTimeout(r, PAUSE_MS));
      }
    };
    const n = Math.min(CONCURRENCY, this.articles.length);
    this.done = Promise.all(Array.from({ length: n }, worker)).then(() => {
      this.finished = true;
      this.articles.filter((a) => a.status === 'pending').forEach((a) => { a.status = 'skipped'; });
      this.emit(null);
    });
    return this.done;
  }

  async lookupOne(a) {
    try {
      const res = this.prefetched && a.id === this.prefetched.current.id ? this.prefetched : await this.lookup(a.id);
      a.item = res.current;
      a.changes = computeChanges(res.current, res.fetched);
      a.unchanged = unchangedFields(res.current, a.changes);
      // Only an opt-in change (a new citation key for an item that has one) is not an update:
      // such articles go with the up-to-date ones, where the key can still be ticked.
      a.status = a.changes.some((c) => c.checked) ? 'changes' : 'same';
    } catch (e) {
      a.status = 'error';
      a.error = e.message;
      a.unchanged = a.item ? unchangedFields(a.item, []) : [];
      if (e.status === 0) this.stopped = true;   // server unreachable: stop instead of failing every item
    }
  }

  stop() { this.stopped = true; }
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
  const job = new LookupJob(ids, { prefetched });
  job.start();
  return reviewDialog(job);
}

/**
 * Retrieve metadata without opening the dialog (e.g. right after an import): progress in a
 * toast, and a "Review" button when the results are in.
 */
export function retrieveInBackground(ids, { references = false } = {}) {
  ids = ids.filter((id) => byId(id));
  if (!ids.length) return null;
  const job = new LookupJob(ids, { references });
  job.start();
  watchInBackground(job);
  return job;
}

function watchInBackground(job) {
  if (!job.running) return finishedToast(job);
  const pt = progressToast('Retrieving metadata…', { onCancel: () => job.stop() });
  const paint = () => pt.set(`Retrieving metadata ${job.looked}/${job.articles.length}${job.withChanges ? ` · ${plural(job.withChanges, 'article')} with updates` : ''}`,
    job.looked, job.articles.length);
  paint();
  const off = job.on((a) => {
    if (a) return paint();
    off();
    pt.close();
    finishedToast(job);
  });
}

function finishedToast(job) {
  if (job.applied) return;
  const n = job.withChanges, failed = job.count('error'), skipped = job.count('skipped');
  const parts = [n ? `Metadata: ${plural(n, 'article')} with updates` : 'Metadata is up to date'];
  if (job.count('same') && n) parts.push(`${job.count('same')} up to date`);
  if (failed) parts.push(`${failed} not found`);
  if (skipped) parts.push(`stopped, ${skipped} not looked up`);
  if (!n) return toast(parts.join(' · '), { type: failed && !job.count('same') ? 'error' : 'success', timeout: 7000 });
  // stays until dismissed, so the results can't be missed
  toast(parts.join(' · '), { type: 'success', timeout: 0, action: { label: 'Review', run: () => reviewDialog(job) } });
}

/* ----------------------------------------------------------------- dialog */
function reviewDialog(job) {
  const { articles } = job;
  let applyBtn, list, summary, stopBtn, bgBtn, groups, offJob, background = false;

  const refreshSummary = () => {
    const done = job.looked;
    const skipped = job.count('skipped');
    const withChanges = job.withChanges;
    const ticked = articles.reduce((n, a) => n + a.changes.filter((c) => c.checked).length, 0);
    const failed = job.count('error');
    const running = job.running && !job.stopped;
    summary.innerHTML = `${running ? '<span class="spinner"></span>' : icon('check', 'icon-sm')}
      <span>${running ? `Looking up ${done} of ${articles.length}…` : `Looked up ${done} of ${plural(articles.length, 'article')}`}</span>
      <span class="mr-dot">·</span><span>${plural(withChanges, 'article')} with updates</span>
      ${failed ? `<span class="mr-dot">·</span><span class="mr-fail">${failed} not found</span>` : ''}
      ${skipped ? `<span class="mr-dot">·</span><span>stopped, ${skipped} not looked up</span>` : ''}
      <span class="spacer"></span><b>${plural(ticked, 'change')} selected</b>`;
    stopBtn.hidden = !running || articles.length === 1;
    bgBtn.hidden = !job.running || articles.length === 1;
    applyBtn.disabled = ticked === 0 || job.running;
    applyBtn.textContent = ticked ? `Apply ${plural(ticked, 'Change')}` : 'Apply';
    const same = job.count('same');
    groups.same.hidden = !same;
    groups.same.querySelector('.mr-group-count').textContent = same;
    groups.error.hidden = !failed;
    groups.error.querySelector('.mr-group-count').textContent = failed;
  };

  // Where each article goes: changes on top, then the ones still pending, then those not
  // found, and the ones already up to date folded away at the end.
  const groupOf = (a) => ({ changes: 'changes', pending: 'pending', skipped: 'pending', error: 'error', same: 'same' }[a.status]);
  const renderArticle = (a) => {
    list.querySelector(`[data-article="${a.id}"]`)?.remove();
    const el = articleSection(a, refreshSummary);
    const box = groups[groupOf(a)].querySelector('.mr-group-items') || groups[groupOf(a)];
    const after = [...box.children].find((c) => Number(c.dataset.n) > a.n);
    box.insertBefore(el, after || null);
  };

  return openDialog({
    title: articles.length === 1 ? 'Review Metadata Updates' : `Review Metadata for ${articles.length} Articles`,
    subtitle: 'Untick anything you want to keep as it is, or edit a value before applying. Nothing is saved until you apply.',
    wide: true,
    render: (body, close, buttons) => {
      applyBtn = buttons[buttons.length - 1];
      body.closest('.dialog').classList.add('mr-dialog');
      body.innerHTML = `<div class="mr-summary"></div><div class="mr-list">
          <div class="mr-group" data-group="changes"></div>
          <div class="mr-group" data-group="pending"></div>
          <div class="mr-group" data-group="error" hidden><div class="mr-group-head">${icon('alert', 'icon-sm')} Not found (<span class="mr-group-count"></span>)</div><div class="mr-group-items"></div></div>
          <details class="mr-group mr-same" data-group="same" hidden><summary class="mr-group-head">${icon('check', 'icon-sm')} Already up to date (<span class="mr-group-count"></span>)
            <span class="subtle">— nothing to change</span></summary><div class="mr-group-items"></div></details>
        </div>`;
      summary = body.querySelector('.mr-summary');
      list = body.querySelector('.mr-list');
      groups = Object.fromEntries([...list.querySelectorAll('[data-group]')].map((g) => [g.dataset.group, g]));
      const left = body.closest('.dialog').querySelector('.dialog-footer .left');
      stopBtn = h('<button class="btn btn-sm">Stop</button>');
      stopBtn.onclick = () => { job.stop(); stopBtn.disabled = true; stopBtn.textContent = 'Stopping…'; };
      bgBtn = h('<button class="btn btn-sm" title="Close this window and keep looking up; a notification lets you review when it is done">Continue in Background</button>');
      bgBtn.onclick = () => { background = true; close(null); };
      left.append(stopBtn, bgBtn);
      articles.forEach(renderArticle);
      refreshSummary();
      offJob = job.on((a) => {
        if (!list.isConnected) return;
        if (a) renderArticle(a);
        else articles.filter((x) => x.status === 'skipped').forEach(renderArticle);
        refreshSummary();
      });
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
      { label: 'Cancel', value: null },
      {
        label: 'Apply', kind: 'primary',
        onClick: async (close, btn) => {
          btn.disabled = true;
          btn.textContent = 'Saving…';
          const ok = await applyChanges(articles, job.references);
          if (!ok) { btn.disabled = false; btn.textContent = 'Apply'; return false; }
          job.applied = true;
          return undefined;
        },
      },
    ],
  }).then((v) => {
    offJob?.();
    if (background) watchInBackground(job);
    else if (job.running) job.stop();     // closed while looking up: stop
    return v;
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
    same: a.changes.length ? '<span class="badge ok">Up to date</span> <span class="badge">new citation key available</span>' : '<span class="badge ok">Up to date</span>',
    error: '<span class="badge err">Not found</span>',
    skipped: '<span class="badge">Not looked up</span>',
  }[a.status];
  const el = h(`<section class="mr-article ${a.status}" data-article="${a.id}" data-n="${a.n}">
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
async function applyChanges(articles, withReferences = false) {
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
  await A.refreshItems(articles.map((a) => a.id));   // just the reviewed items (a full reload only where needed)
  // the reference lists come with the metadata (in the background; see Settings → Online Services)
  const looked = articles.filter((a) => a.status === 'changes' || a.status === 'same').map((a) => a.id);
  if (looked.length && (withReferences || state.settings.references_auto !== false)) api.queueReferences(looked).catch(() => {});
  if (errors.length) {
    toastError(new Error(`${errors.length} could not be saved — ${errors[0]}`));
    return saved > 0;
  }
  toast(saved === 1 ? `Updated ${plural(fields, 'field')}` : `Updated ${plural(fields, 'field')} in ${plural(saved, 'article')}`, { type: 'success' });
  return true;
}
