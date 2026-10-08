// Left sidebar: library / trash and full-text index status.
import * as A from '../actions.js';
import { icon } from '../icons.js';
import { on, state } from '../store.js';
import { openMenu } from '../ui/menu.js';

let root;

export function initSidebar() {
  root = document.getElementById('sidebar');
  root.innerHTML = `
    <div class="side-section">Library</div>
    <button class="side-item" data-view="library">${icon('library')}<span>My Library</span><span class="count" data-count="library"></span></button>
    <button class="side-item" data-view="duplicates" title="Items with the same DOI, or the same title and year">${icon('copy')}<span>Duplicate Items</span><span class="count" data-count="duplicates"></span></button>
    <button class="side-item" data-view="trash">${icon('trash')}<span>Trash</span><span class="count" data-count="trash"></span></button>
    <div class="side-footer"><div class="row" data-index-status></div></div>`;
  root.addEventListener('click', (e) => {
    const b = e.target.closest('[data-view]');
    if (b) A.setView(b.dataset.view);
  });
  root.addEventListener('contextmenu', (e) => {
    const b = e.target.closest('[data-view="trash"]');
    if (!b) return;
    e.preventDefault();
    openMenu([{ label: 'Empty Trash…', icon: 'trash', danger: true, disabled: !state.counts.trash, run: A.emptyTrash }], { x: e.clientX, y: e.clientY });
  });
  // drag items onto Trash? (drop files onto My Library = add as items)
  const lib = root.querySelector('[data-view="library"]');
  lib.addEventListener('dragover', (e) => { if ([...e.dataTransfer.types].includes('Files')) { e.preventDefault(); lib.classList.add('drop-target'); } });
  lib.addEventListener('dragleave', () => lib.classList.remove('drop-target'));
  lib.addEventListener('drop', (e) => { e.preventDefault(); lib.classList.remove('drop-target'); A.addPdfsAsItems(e.dataTransfer.files); });

  on('items', paint);
  on('view', paint);
  on('indexer', paintIndexer);
  paint();
}

function paint() {
  root.querySelectorAll('[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === state.view));
  root.querySelector('[data-count="library"]').textContent = state.counts.library || '';
  root.querySelector('[data-count="trash"]').textContent = state.counts.trash || '';
  root.querySelector('[data-count="duplicates"]').textContent = state.counts.duplicates || '';
}

function paintIndexer() {
  const ix = state.indexer;
  const el = root.querySelector('[data-index-status]');
  if (!ix) { el.innerHTML = ''; return; }
  if (ix.indexer.running) {
    el.innerHTML = `<span class="spinner"></span> Indexing PDFs ${ix.indexer.done}/${ix.indexer.total}`;
  } else {
    el.innerHTML = `${icon('text', 'icon-sm')} ${ix.fulltext.documents}/${ix.attachments} PDFs searchable`;
    el.title = 'Full-text index status (Settings → Search & Full Text)';
  }
}
