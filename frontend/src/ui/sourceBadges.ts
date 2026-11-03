// Paper pills and compact navigation indicators share colours and accessible explanations.
import { icon } from '../icons.js';
import type { SourceBadge } from '../types.js';
import { escapeHtml } from '../util.js';

export function sourceBadges(badges: SourceBadge[] = [], compact = false): string {
  if (!badges.length) return '';
  return `<span class="source-badges">${badges.map((b) => {
    const tone = b.tone === 'teal' ? 'teal' : 'gold';
    return `<span class="source-badge ${tone} ${compact ? 'compact' : 'row-highlight'}" role="img" title="${escapeHtml(b.text)}" aria-label="${escapeHtml(b.text)}">${compact ? icon(b.icon === 'link' ? 'link' : 'star', 'icon-sm') : escapeHtml(b.label)}</span>`;
  }).join('')}</span>`;
}
