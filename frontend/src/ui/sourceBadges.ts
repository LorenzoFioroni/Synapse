// Paper pills and compact navigation indicators share colours and accessible explanations.
import { icon } from '../icons.js';
import type { PluginSource, SourceBadge } from '../types.js';
import { escapeHtml } from '../util.js';

export function sourceBadges(badges: SourceBadge[] = [], compact = false): string {
  if (!badges.length) return '';
  return `<span class="source-badges">${badges.map((b) => {
    const tone = b.tone === 'red' ? 'red' : b.tone === 'teal' ? 'teal' : 'gold';
    return `<span class="source-badge ${tone} ${compact ? 'compact' : 'row-highlight'}" role="img" title="${escapeHtml(b.text)}" aria-label="${escapeHtml(b.text)}">${compact ? icon(b.icon === 'megaphone' ? 'megaphone' : b.icon === 'link' ? 'link' : 'star', 'icon-sm') : escapeHtml(b.label)}</span>`;
  }).join('')}</span>`;
}

/** Navigation reflects the browser's current calendar day, even when browsing older entries. */
export function todaySourceBadges(variants: PluginSource['variants'], now = new Date()): SourceBadge[] {
  const day = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  return variants?.find((v) => v.id === day)?.highlights || [];
}
