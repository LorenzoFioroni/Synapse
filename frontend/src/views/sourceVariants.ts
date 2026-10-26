// A source with variants keeps one entry in the main navigation. Its variants live in this
// second pane beside the item list, like the note selector beside the note editor and graph.
import * as A from '../actions.js';
import { currentSource, on, state } from '../store.js';
import { escapeHtml } from '../util.js';

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
      <div class="source-variants-list">${variants.map((variant, index) => `
        <button class="source-variant-item ${variant.id === selected ? 'active' : ''}" data-source-variant="${escapeHtml(variant.id)}">
          <span class="source-variant-name">${escapeHtml(index === 0 ? 'Today' : variant.title)}</span>
          ${index === 0 ? `<span class="source-variant-date">${escapeHtml(variant.title)}</span>` : ''}
          <span class="source-variant-count">${escapeHtml(variant.badge || '')}</span>
        </button>`).join('')}</div>`;
  };

  root.addEventListener('click', (event) => {
    const button = (event.target as Element).closest<HTMLElement>('[data-source-variant]');
    const source = currentSource();
    if (button?.dataset.sourceVariant && source) A.setView(`source:${source.id}~${button.dataset.sourceVariant}`);
  });
  on('view', paint);
  on('sources', paint);
  paint();
}
