// Galleries in an exported note (views/notes/exportNote.ts calls staticGalleries through the
// FiguresHook): a carousel only works in the app, so each becomes one figure.
import { figureLabel } from './figures.js';
import { endsSentence, figureColumns, panelLabel } from './gallery.js';

/**
 * Each gallery's carousel becomes a figure: the panels in rows of at most three (figureColumns),
 * each labelled a, b, … in a circle on its upper left corner, and one caption, the gallery's own followed by the panels':
 * "Caption. (a) First panel. (b) Second panel." A gallery of one image has no labels.
 */
export function staticGalleries(root: HTMLElement): void {
  for (const g of root.querySelectorAll<HTMLElement>('figure.gallery')) {
    const panels = [...g.querySelectorAll<HTMLElement>('.gallery-panel')];
    if (!panels.length) { g.remove(); continue; }
    const el = (tag: string, cls: string) => { const e = document.createElement(tag); e.className = cls; return e; };
    const fig = el('figure', `gallery-figure cols-${figureColumns(panels.length)}`);
    if (g.style.maxWidth) fig.style.maxWidth = g.style.maxWidth;
    // the figure's number goes once in front of the whole caption, not on each panel
    g.querySelectorAll('.fig-num').forEach((n) => {
      if (n.nextSibling?.nodeType === Node.TEXT_NODE) n.nextSibling.textContent = n.nextSibling.textContent!.replace(/^\s+/, '');
      n.remove();
    });
    fig.append(...g.querySelectorAll(':scope > .eq-anchor'));   // the targets of \ref
    const grid = el('div', 'gallery-grid');
    const caption = el('figcaption', 'gallery-figcaption');
    // a part of the caption: its rendered content, ending as a sentence
    const addPart = (from: Element | null, label: string | null) => {
      if (!from || !from.textContent?.trim()) return;
      if (caption.childNodes.length) caption.append(' ');
      if (label) { caption.append(el('span', 'gallery-ref')); caption.lastElementChild!.textContent = `(${label})`; caption.append(' '); }
      caption.append(...from.childNodes);
      if (!endsSentence(caption.textContent || '')) caption.append('.');
    };
    addPart(g.querySelector('.gallery-caption'), null);
    const single = panels.length === 1;
    panels.forEach((p, i) => {
      const cell = el('div', 'gallery-cell');
      cell.append(...p.querySelectorAll(':scope > .eq-anchor'));   // a panel's \label
      // the image in a frame that hugs it, so the label sits on its upper left corner
      const frame = el('div', 'gallery-cell-image');
      const image = p.querySelector('.gallery-image');
      if (image) frame.append(...image.childNodes);
      if (!single) { const badge = el('span', 'gallery-cell-label'); badge.textContent = panelLabel(i); frame.append(badge); }
      cell.append(frame);
      grid.append(cell);
      const text = p.querySelector('.gallery-caption-text') || p.querySelector('.gallery-panel-caption');
      const label = text?.querySelector('.gallery-label');
      if (label?.nextSibling?.nodeType === Node.TEXT_NODE) label.nextSibling.textContent = label.nextSibling.textContent!.replace(/^\s+/, '');
      label?.remove();
      addPart(text, single ? null : panelLabel(i));
    });
    fig.append(grid);
    if (caption.childNodes.length) {
      if (g.dataset.fig) { const num = el('span', 'fig-num'); num.textContent = `${figureLabel(Number(g.dataset.fig))}.`; caption.prepend(num, ' '); }
      fig.append(caption);
    }
    g.replaceWith(fig);
  }
}
