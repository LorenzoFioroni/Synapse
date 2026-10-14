// @ts-nocheck
// Context / dropdown menus with keyboard navigation.
import { icon } from '../icons.js';
import { escapeHtml, h } from '../util.js';

let current = null;

export function closeMenu() {
  if (current) { current.remove(); current = null; document.removeEventListener('keydown', onKey, true); }
}

function onKey(e) {
  if (!current) return;
  const items = [...current.querySelectorAll('.menu-item:not(:disabled)')];
  let i = items.findIndex((x) => x.classList.contains('focused'));
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(); return; }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault(); e.stopPropagation();
    i = e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
    items.forEach((x) => x.classList.remove('focused'));
    items[i]?.classList.add('focused');
  }
  if (e.key === 'Enter' && i >= 0) { e.preventDefault(); e.stopPropagation(); items[i].click(); }
}

/**
 * entries: [{label, icon, shortcut, danger, disabled, checked, run}] | 'sep' | {section: 'Label'}
 * at: {x, y} or an element to anchor below.
 */
export function openMenu(entries, at) {
  closeMenu();
  const el = h('<div class="menu" role="menu"></div>');
  for (const e of entries) {
    if (!e) continue;
    if (e === 'sep') { el.appendChild(h('<div class="menu-sep"></div>')); continue; }
    if (e.section) { el.appendChild(h(`<div class="menu-label">${escapeHtml(e.section)}</div>`)); continue; }
    const b = h(`<button class="menu-item ${e.danger ? 'danger' : ''}" role="menuitem" ${e.disabled ? 'disabled' : ''}>
        ${e.checked !== undefined ? `<span class="check">${e.checked ? '✓' : ''}</span>` : (e.icon ? icon(e.icon) : '')}
        <span>${escapeHtml(e.label)}</span>${e.shortcut ? `<span class="shortcut">${escapeHtml(e.shortcut)}</span>` : ''}</button>`);
    b.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (!e.keepOpen) closeMenu();
      e.run?.(b);
      if (e.keepOpen && e.checked !== undefined) {
        e.checked = !e.checked;
        b.querySelector('.check').textContent = e.checked ? '✓' : '';
      }
    });
    el.appendChild(b);
  }
  document.body.appendChild(el);
  let x, y;
  if (at instanceof Element) {
    const r = at.getBoundingClientRect();
    x = r.left; y = r.bottom + 4;
  } else ({ x, y } = at);
  const mw = el.offsetWidth, mh = el.offsetHeight;
  if (x + mw > innerWidth - 8) x = Math.max(8, innerWidth - mw - 8);
  if (y + mh > innerHeight - 8) y = Math.max(8, (at instanceof Element ? at.getBoundingClientRect().top - mh - 4 : y - mh));
  el.style.left = `${x}px`; el.style.top = `${y}px`;
  current = el;
  document.addEventListener('keydown', onKey, true);
  return el;
}

document.addEventListener('pointerdown', (e) => { if (current && !current.contains(e.target)) closeMenu(); });
window.addEventListener('blur', closeMenu);
window.addEventListener('resize', closeMenu);
