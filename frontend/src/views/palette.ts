// The command palette (⌘⇧P / Ctrl+Shift+P, or F1): every command of Synapse and of its plugins,
// found by typing a few letters of its name. Recently used commands come first.
import { commands, keyLabel, rankCommands } from '../extend.js';
import { escapeHtml, h, isMac, storage, store } from '../util.js';
import { toastError } from '../ui/toast.js';

const RECENT_KEY = 'zc.palette.recent';
let open: HTMLElement | null = null;

export const paletteOpen = (): boolean => !!open;

export function closePalette(): void {
  open?.remove();
  open = null;
}

export function openPalette(): void {
  if (open) { open.querySelector('input')?.focus(); return; }
  const prevFocus = document.activeElement as HTMLElement | null;
  const back = h(`<div class="dialog-backdrop palette-backdrop">
    <div class="palette" role="dialog" aria-modal="true" aria-label="Commands">
      <input class="palette-input" type="text" placeholder="Type a command" spellcheck="false" autocomplete="off" aria-label="Command">
      <div class="palette-list" role="listbox"></div>
    </div></div>`);
  open = back;
  const input = back.querySelector<HTMLInputElement>('input')!;
  const list = back.querySelector<HTMLElement>('.palette-list')!;
  let shown = rankCommands(commands.all(), '', storage<string[]>(RECENT_KEY, []));
  let cur = 0;

  const paint = () => {
    list.innerHTML = shown.length ? shown.map((c, i) => `<button class="palette-item ${i === cur ? 'active' : ''}" role="option" data-i="${i}" aria-selected="${i === cur}">
        <span class="p-title">${c.source ? `<span class="p-source">${escapeHtml(c.source)}:</span> ` : ''}${escapeHtml(c.title)}</span>
        ${c.key ? `<span class="shortcut">${escapeHtml(keyLabel(c.key, isMac))}</span>` : ''}</button>`).join('')
      : '<div class="palette-empty">No command matches.</div>';
    list.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
  };
  const run = (i: number) => {
    const c = shown[i];
    if (!c) return;
    close();
    store(RECENT_KEY, [c.id, ...storage<string[]>(RECENT_KEY, []).filter((x) => x !== c.id)].slice(0, 20));
    try {
      const r = c.run();
      if (r instanceof Promise) r.catch(toastError);
    } catch (e) { toastError(e); }
  };
  const close = () => {
    closePalette();
    if (prevFocus?.isConnected) prevFocus.focus();
  };

  input.addEventListener('input', () => { shown = rankCommands(commands.all(), input.value, storage<string[]>(RECENT_KEY, [])); cur = 0; paint(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); cur = Math.min(shown.length - 1, cur + 1); paint(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); cur = Math.max(0, cur - 1); paint(); }
    else if (e.key === 'Enter') { e.preventDefault(); run(cur); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
  });
  list.addEventListener('click', (e) => {
    const b = (e.target as Element).closest<HTMLElement>('[data-i]');
    if (b) run(Number(b.dataset.i));
  });
  list.addEventListener('mousemove', (e) => {
    const b = (e.target as Element).closest<HTMLElement>('[data-i]');
    if (b && Number(b.dataset.i) !== cur) { cur = Number(b.dataset.i); list.querySelectorAll('.palette-item').forEach((x, i) => x.classList.toggle('active', i === cur)); }
  });
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  document.body.appendChild(back);
  paint();
  input.focus();
}
