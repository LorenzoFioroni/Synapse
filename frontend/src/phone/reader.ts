// A PDF on the phone: the app's viewer (viewer.html) over the whole screen, under a thin bar with
// the paper's name and a way back. The viewer keeps its own tools (highlighting, quoting into a
// note, its page list).
import { type ViewerSpot, viewerUrl } from '../api.js';
import { icon } from '../icons.js';
import type { Attachment, Item } from '../types.js';
import { escapeHtml as esc } from '../util.js';
import { familyNames } from './model.js';
import type { Screen } from './shell.js';
import { navBar } from './ui.js';

export function readerScreen(item: Pick<Item, 'id' | 'title' | 'authors' | 'year'>, att: Pick<Attachment, 'id' | 'url'>, page: number | null = null, at: ViewerSpot = {}): Screen {
  const name = [familyNames(item.authors), item.year ? String(item.year) : ''].filter(Boolean).join(' ') || item.title || 'PDF';
  return {
    title: name,
    key: `pdf:${att.id}`,
    mount(el, ctx) {
      el.classList.add('ph-reader');
      el.innerHTML = `${navBar(ctx.backLabel, name, `<a class="ph-nav-btn" href="${esc(viewerUrl(item, att, page, at))}" target="_blank" rel="noopener" aria-label="Open in a browser tab" title="Open in a browser tab">${icon('newWindow')}</a>`)}
        <iframe class="ph-reader-frame" title="${esc(item.title || 'PDF')}" src="${esc(viewerUrl(item, att, page, at))}"></iframe>`;
    },
  };
}
