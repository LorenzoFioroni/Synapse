// Editable string lists: native text fields, Add/Remove buttons, and Enter to add a row.
import { icon } from '../icons.js';
import { escapeHtml } from '../util.js';

function row(value: string, label: string): string {
  return `<div class="string-list-row" role="listitem"><input class="input" type="text" value="${escapeHtml(value)}" aria-label="${escapeHtml(label)} entry" spellcheck="false"><button type="button" class="icon-btn" data-list-remove aria-label="Remove ${escapeHtml(label)} entry" title="Remove entry">${icon('close', 'icon-sm')}</button></div>`;
}

export function stringList(label: string, values: string[], attrs: string): string {
  return `<div class="string-list" ${attrs} data-list-label="${escapeHtml(label)}"><div class="string-list-rows" role="list" aria-label="${escapeHtml(label)}">${values.map((v) => row(v, label)).join('')}</div><button type="button" class="btn btn-sm" data-list-add aria-label="Add ${escapeHtml(label)} entry">${icon('plus', 'icon-sm')} Add entry</button></div>`;
}

export function stringListValues(holder: HTMLElement): string[] {
  return [...new Set([...holder.querySelectorAll<HTMLInputElement>('.string-list-row input')].map((i) => i.value.trim()).filter(Boolean))];
}

function add(holder: HTMLElement, after?: HTMLElement): void {
  const empty = [...holder.querySelectorAll<HTMLInputElement>('input')].find((i) => !i.value.trim());
  if (empty) { empty.focus(); return; }
  const html = row('', holder.dataset.listLabel || 'List');
  if (after) after.insertAdjacentHTML('afterend', html);
  else holder.querySelector('.string-list-rows')!.insertAdjacentHTML('beforeend', html);
  (after?.nextElementSibling || holder.querySelector('.string-list-row:last-child'))?.querySelector('input')?.focus();
}

export function bindStringLists(root: HTMLElement): void {
  root.addEventListener('click', (event) => {
    const target = event.target as Element;
    const holder = target.closest<HTMLElement>('.string-list');
    if (!holder) return;
    if (target.closest('[data-list-add]')) add(holder);
    if (target.closest('[data-list-remove]')) {
      const current = target.closest<HTMLElement>('.string-list-row')!;
      const next = current.nextElementSibling || current.previousElementSibling;
      current.remove();
      (next?.querySelector('input') || holder.querySelector<HTMLButtonElement>('[data-list-add]'))?.focus();
      holder.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  root.addEventListener('keydown', (event) => {
    const input = event.target as HTMLInputElement;
    const holder = input.closest<HTMLElement>('.string-list');
    if (holder && input.matches('input') && event.key === 'Enter') {
      event.preventDefault();
      input.dispatchEvent(new Event('change', { bubbles: true }));
      add(holder, input.closest<HTMLElement>('.string-list-row')!);
    }
  });
}
