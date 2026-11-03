// A source with variants keeps one entry in the main navigation. Its variants live in this
// second pane beside the item list, like the note selector beside the note editor and graph.
import * as A from '../actions.js';
import { currentSource, on, state } from '../store.js';
import { sourceBadges } from '../ui/sourceBadges.js';
import { escapeHtml } from '../util.js';

export function isToday(id: string, now = new Date()): boolean {
  return id === `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

export function initSourceVariants(root: HTMLElement): void {
  const paint = () => {
    const source = currentSource();
    const variants = source?.variants || [];
    root.hidden = !variants.length;
    if (!variants.length || !source) {
      root.innerHTML = '';
      return;
    }
    const selected = state.view.includes('~') ? state.view.slice(state.view.indexOf('~') + 1) : variants[0]?.id;
    root.innerHTML = `<div class="source-variants-title">${escapeHtml(source.title)}</div>
      <div class="source-variants-list">${variants.map((variant) => `
        <button class="source-variant-item ${variant.id === selected ? 'active' : ''}" data-source-variant="${escapeHtml(variant.id)}">
          <span class="source-variant-name">${escapeHtml(isToday(variant.id) ? 'Today' : variant.title)}</span>
          ${isToday(variant.id) ? `<span class="source-variant-date">${escapeHtml(variant.title)}</span>` : ''}
          ${sourceBadges(variant.highlights, true)}
          <span class="source-variant-count">${escapeHtml(variant.badge || '')}</span>
        </button>`).join('')}</div>`;
  };

  root.addEventListener('click', (event) => {
    const button = (event.target as Element).closest<HTMLElement>('[data-source-variant]');
    const source = currentSource();
    if (button?.dataset.sourceVariant && source) A.setView(`source:${source.id}~${button.dataset.sourceVariant}`);
  });
  // Refresh clock-dependent labels even when no new feed or navigation event arrives.
  window.setInterval(paint, 60_000);
  document.addEventListener('visibilitychange', paint);
  on('view', paint);
  on('sources', paint);
  paint();
}
