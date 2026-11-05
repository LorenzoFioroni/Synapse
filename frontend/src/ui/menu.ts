// Context / dropdown menus with keyboard navigation.
import { icon } from '../icons.js';
import { escapeHtml, h } from '../util.js';

/** A menu entry, a separator ('sep'), a section label ({section}) or a skipped slot (null/false). */
export type MenuEntry = MenuItem | 'sep' | { section: string } | null | undefined | false;
export interface MenuItem {
  label: string;
  icon?: string;
  shortcut?: string;
  danger?: boolean;
  disabled?: boolean;
  checked?: boolean;     // shows a check column; with keepOpen the menu toggles it
  /** A colour swatch before the label (#rrggbb; '' for an empty one: no colour). */
  swatch?: string;
  keepOpen?: boolean;
  run?: (button: HTMLElement) => void;
}

let current: HTMLElement | null = null;
let backdrop: HTMLElement | null = null;
/** Phones show every menu as a sheet from the bottom of the screen (phone/main.ts sets this). */
let asSheets = false;
export function menusAsSheets(on: boolean): void { asSheets = on; }

export function closeMenu() {
  if (current) { current.remove(); current = null; document.removeEventListener('keydown', onKey, true); }
  if (backdrop) { backdrop.remove(); backdrop = null; }
}

function onKey(e: KeyboardEvent) {
  if (!current) return;
  const items = [...current.querySelectorAll<HTMLElement>('.menu-item:not(:disabled)')];
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
export function openMenu(entries: MenuEntry[], at: { x: number; y: number } | Element): HTMLElement {
  closeMenu();
  const el = h('<div class="menu" role="menu"></div>');
  for (const e of entries) {
    if (!e) continue;
    if (e === 'sep') { el.appendChild(h('<div class="menu-sep"></div>')); continue; }
    if ('section' in e) { el.appendChild(h(`<div class="menu-label">${escapeHtml(e.section)}</div>`)); continue; }
    const b = h(`<button class="menu-item ${e.danger ? 'danger' : ''}" role="menuitem" ${e.disabled ? 'disabled' : ''}>
        ${e.checked !== undefined ? `<span class="check">${e.checked ? '✓' : ''}</span>` : (e.icon ? icon(e.icon) : '')}
        ${e.swatch !== undefined ? `<span class="menu-swatch${e.swatch ? '' : ' none'}"${e.swatch ? ` style="background:${escapeHtml(e.swatch)}"` : ''}></span>` : ''}<span>${escapeHtml(e.label)}</span>${e.shortcut ? `<span class="shortcut">${escapeHtml(e.shortcut)}</span>` : ''}</button>`);
    b.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (!e.keepOpen) closeMenu();
      e.run?.(b);
      if (e.keepOpen && e.checked !== undefined) {
        e.checked = !e.checked;
        b.querySelector('.check')!.textContent = e.checked ? '✓' : '';
      }
    });
    el.appendChild(b);
  }
  if (asSheets) {
    // a sheet: the entries from the bottom edge, over a dimmed page that closes it when tapped
    backdrop = h('<div class="menu-sheet-backdrop"></div>');
    backdrop.addEventListener('click', (ev) => { ev.stopPropagation(); closeMenu(); });
    el.classList.add('menu-sheet');
    el.insertBefore(h('<div class="sheet-grabber" aria-hidden="true"></div>'), el.firstChild);
    document.body.append(backdrop, el);
    current = el;
    document.addEventListener('keydown', onKey, true);
    return el;
  }
  document.body.appendChild(el);
  let x: number, y: number;
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

// a sheet's backdrop closes it on click, so the tap that closes it does not reach the page beneath
document.addEventListener('pointerdown', (e) => { if (current && !current.contains(e.target as Node) && e.target !== backdrop) closeMenu(); });
window.addEventListener('blur', closeMenu);
// (a sheet stays: on a phone the window resizes whenever the keyboard comes or goes)
window.addEventListener('resize', () => { if (!asSheets) closeMenu(); });
