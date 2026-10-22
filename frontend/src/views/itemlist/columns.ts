// The item list's header: which columns show, their order and widths (drag to reorder, drag the
// grip to resize, double-click it to fit), and sorting by a click on a column.
import { icon } from '../../icons.js';
import { COLUMNS, FIELD_COL, type ColumnDef, colDef, emit, saveColumns, saveSort, state } from '../../store.js';
import { openMenu, type MenuEntry } from '../../ui/menu.js';
import { escapeHtml } from '../../util.js';

const TITLE: ColumnDef = COLUMNS.find((c) => c.id === 'title')!;
/** A column's definition (a field's column has none while its plugin is off). */
const def = (id: string): ColumnDef => colDef(id) || TITLE;

let table: HTMLElement, head: HTMLElement, body: HTMLElement;
let resorted: () => void = () => {};

/** Set up the header. ``onSort`` redraws the rows after the sort order changed. */
export function initColumns(els: { table: HTMLElement; head: HTMLElement; body: HTMLElement }, onSort: () => void): void {
  ({ table, head, body } = els);
  resorted = onSort;
  renderHead();
  bindHead();
}

/** The columns shown: those turned on, except a plugin field's while its plugin is off. */
export function visibleCols() { return state.columns.filter((c) => c.visible && colDef(c.id)); }

function applyGrid(): void {
  const tpl = visibleCols().map((c) => (c.id === 'title' ? `minmax(${c.width}px, 1fr)` : `${c.width}px`)).join(' ');
  table.style.setProperty('--cols', tpl);
  table.style.minWidth = `${visibleCols().reduce((n, c) => n + c.width, 0)}px`;
}

export function renderHead(): void {
  const { col, dir } = state.sort;
  head.innerHTML = visibleCols().map((c) => {
    const d = def(c.id);
    const sorted = col === c.id;
    const label = c.id === 'attachments' ? icon('paperclip', 'icon-sm')
      : c.id === 'notes' ? icon('edit', 'icon-sm') : `<span class="label">${escapeHtml(d.short || d.label)}</span>`;
    return `<div class="head-cell ${sorted ? 'sorted' : ''}" data-col="${escapeHtml(c.id)}" draggable="${d.fixed ? 'false' : 'true'}" title="${escapeHtml(d.label)}">
      ${label}${sorted ? `<span class="sort">${dir === 'asc' ? '▲' : '▼'}</span>` : ''}
      ${d.fixed ? '' : '<span class="grip" data-grip></span>'}</div>`;
  }).join('');
  applyGrid();
}

function bindHead(): void {
  let dragId: string | null = null;
  const cellOf = (e: Event) => (e.target as Element).closest<HTMLElement>('.head-cell');
  const gripOf = (e: Event) => (e.target as Element).closest<HTMLElement>('[data-grip]');
  head.addEventListener('click', (e) => {
    if (gripOf(e)) return;
    const cell = cellOf(e);
    if (!cell) return;
    const id = cell.dataset.col!;
    if (state.sort.col === id) state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
    else state.sort = { col: id, dir: ['date_added', 'date_modified', 'year'].includes(id) || id.startsWith(FIELD_COL) ? 'desc' : 'asc' };
    saveSort();
    renderHead();
    resorted();
  });
  head.addEventListener('contextmenu', (e) => { e.preventDefault(); openColumnPicker({ x: e.clientX, y: e.clientY }); });

  // resize
  head.addEventListener('mousedown', (e) => {
    const grip = gripOf(e);
    if (!grip) return;
    e.preventDefault(); e.stopPropagation();
    const id = grip.parentElement!.dataset.col!;
    const col = state.columns.find((c) => c.id === id);
    if (!col) return;
    const startX = e.clientX, startW = col.width, min = def(id).min || 40;
    document.body.style.cursor = 'col-resize';
    const move = (ev: MouseEvent) => { col.width = Math.max(min, Math.round(startW + ev.clientX - startX)); applyGrid(); };
    const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); document.body.style.cursor = ''; saveColumns(); };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
  head.addEventListener('dblclick', (e) => {
    const grip = gripOf(e);
    if (!grip) return;
    autoFit(grip.parentElement!.dataset.col!);
  });

  // reorder
  head.addEventListener('dragstart', (e) => {
    const cell = cellOf(e);
    if (!cell || gripOf(e) || !e.dataTransfer) { e.preventDefault(); return; }
    dragId = cell.dataset.col!;
    cell.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/x-zc-column', dragId);
  });
  head.addEventListener('dragover', (e) => {
    if (!dragId) return;
    e.preventDefault();
    head.querySelectorAll('.drag-over').forEach((c) => c.classList.remove('drag-over'));
    cellOf(e)?.classList.add('drag-over');
  });
  head.addEventListener('drop', (e) => {
    if (!dragId) return;
    e.preventDefault();
    const target = cellOf(e)?.dataset.col;
    if (target && target !== dragId) {
      const from = state.columns.findIndex((c) => c.id === dragId);
      const [moved] = state.columns.splice(from, 1);
      const to = state.columns.findIndex((c) => c.id === target);
      state.columns.splice(to, 0, moved);
      saveColumns();
      emit('columns');
    }
  });
  head.addEventListener('dragend', () => { dragId = null; renderHead(); });
}

function autoFit(id: string): void {
  const col = state.columns.find((c) => c.id === id);
  if (!col) return;
  col.width = 30;            // shrink first so scrollWidth reports the content width
  applyGrid();
  let w = 60;
  body.querySelectorAll(`.cell[data-col="${id}"]`).forEach((c) => { w = Math.max(w, c.scrollWidth + 4); });
  const hc = head.querySelector(`.head-cell[data-col="${id}"] .label`);
  if (hc) w = Math.max(w, hc.scrollWidth + 36);
  col.width = Math.min(w, 900);
  applyGrid();
  saveColumns();
}

export function openColumnPicker(at: { x: number; y: number } | Element): void {
  const entries: MenuEntry[] = [{ section: 'Columns' }];
  const toggle = (c: (typeof state.columns)[number]) => ({
    label: def(c.id).label, checked: c.visible, keepOpen: true,
    run: () => { c.visible = !c.visible; saveColumns(); emit('columns'); },
  });
  for (const c of state.columns) {
    if (c.id === 'title' || c.id.startsWith(FIELD_COL)) continue;
    entries.push(toggle(c));
  }
  const fields = state.columns.filter((c) => c.id.startsWith(FIELD_COL) && colDef(c.id));
  if (fields.length) entries.push({ section: 'From plugins' }, ...fields.map(toggle));
  entries.push('sep', {
    label: 'Reset Columns', icon: 'refresh', run: () => {
      localStorage.removeItem('zc.columns');
      const fresh = [...COLUMNS.map((c) => ({ id: c.id, width: c.width, visible: ['attachments', 'title', 'creator', 'year', 'journal', 'date_added'].includes(c.id) })),
        ...state.columns.filter((c) => c.id.startsWith(FIELD_COL)).map((c) => ({ ...c, visible: false }))];
      state.columns.splice(0, state.columns.length, ...fresh);
      saveColumns(); emit('columns');
    },
  });
  const m = openMenu(entries, at);
  m.style.maxHeight = '70vh'; m.style.overflow = 'auto';
}
