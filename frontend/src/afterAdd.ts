// @ts-nocheck
// What happens right after items are added (Settings → Adding Items). Each action is
// "never", "always" or "ask"; for "ask" the place where items are added (the import dialog,
// Add by Identifier, …) shows a ticked checkbox so the user decides each time.
import * as A from './actions.js';
import { api } from './api.js';
import { byId, state } from './store.js';
import { escapeHtml } from './util.js';

export const ACTIONS = {
  metadata: { key: 'add_fetch_metadata', label: 'Retrieve metadata online', done: 'retrieves metadata' },
  pdf: { key: 'add_find_pdf', label: 'Find available PDFs', done: 'looks for PDFs' },
  references: { key: 'add_fetch_references', label: 'Fetch reference lists', done: 'fetches reference lists' },
  tags: { key: 'import_tags', label: 'Turn keywords into tags', done: 'turns keywords into tags' },
};

export const mode = (kind) => state.settings[ACTIONS[kind].key] || 'ask';

/**
 * The checkboxes for the "ask" actions among `kinds` (ticked), and a line naming the "always"
 * ones. `labels` overrides the label of an action. Returns '' when there is nothing to show.
 */
export function afterAddHtml(kinds, labels = {}) {
  const ask = kinds.filter((k) => mode(k) === 'ask');
  const always = kinds.filter((k) => mode(k) === 'always');
  if (!ask.length && !always.length) return '';
  return `<div class="after-add">
    ${ask.map((k) => `<label class="checkbox"><input type="checkbox" data-after="${k}" checked> ${escapeHtml(labels[k] || ACTIONS[k].label)}</label>`).join('')}
    ${always.length ? `<div class="field-hint">Also ${always.map((k) => ACTIONS[k].done).join(', ')} — Settings → Adding Items.</div>` : ''}
  </div>`;
}

/** {kind: true/false} — what to do, from the settings and the ticked boxes inside `root`. */
export function afterAddChoices(root, kinds) {
  return Object.fromEntries(kinds.map((k) => {
    const m = mode(k);
    const box = root?.querySelector(`[data-after="${k}"]`);
    return [k, m === 'always' || (m === 'ask' && (box ? box.checked : false))];
  }));
}

const hasIdentifier = (it) => !!(it && (it.doi || /arxiv/i.test(`${it.archive_id || ''} ${it.url || ''} ${it.extra || ''}`)));

/**
 * Run the chosen follow-ups on new items. All of them run in the background with their own
 * progress, so the user can keep working.
 */
export async function runAfterAdd(ids, { metadata = false, pdf = false, references = false } = {}) {
  ids = ids.filter((id) => byId(id));
  if (!ids.length) return;
  if (references) {
    const withId = ids.filter((id) => hasIdentifier(byId(id)));
    if (withId.length) api.queueReferences(withId).catch(() => {});
  }
  if (metadata) {
    const { retrieveInBackground } = await import('./views/metadataReview.js');
    // items that only get their DOI from the lookup fetch their references once it is applied
    retrieveInBackground(ids, { references });
  }
  if (pdf) A.findPdfs(ids, { auto: true });
}
