// Choosing a colour for the annotation tools and for a selected annotation: a row of swatches,
// "None" where a colour can be left out (a box's border or fill), and any other colour with the
// system's picker. Used by the toolbar (prefs.ts) and by the controls of a selected annotation
// (input.ts). Looks: .zc-swatch… in viewer.html.

export const SWATCHES = ['#ffd400', '#ff6666', '#5fb236', '#2ea8e5', '#a28ae5', '#000000'];

export interface SwatchRow {
  /** Shown before the swatches (a box has two rows: Border and Fill). */
  label?: string;
  /** The current colour; null for none. */
  value: string | null;
  /** Offers "None"; disabled (the other part would be left out too) when 'disabled'. */
  none?: boolean | 'disabled';
  /** A colour chosen (null: none). */
  onPick: (c: string | null) => void;
  /** The system picker's colour while it is open (saved with onPick when it closes). */
  onPreview?: (c: string) => void;
}

/** A row of swatches for one colour. */
export function swatchRow(o: SwatchRow): HTMLElement {
  const row = document.createElement('div');
  row.className = 'zc-swatch-row';
  const current = o.value?.toLowerCase() ?? null;
  row.innerHTML = (o.label ? `<span class="zc-swatch-label">${o.label}</span>` : '')
    + SWATCHES.map((c) => `<button class="zc-swatch${c === current ? ' active' : ''}" data-color="${c}" title="${c}" style="--swatch:${c}"></button>`).join('')
    + (o.none ? `<button class="zc-swatch zc-swatch-none${current === null ? ' active' : ''}" data-none title="None"${o.none === 'disabled' ? ' disabled' : ''}></button>` : '')
    + `<label class="zc-swatch zc-swatch-custom${current && !SWATCHES.includes(current) ? ' active' : ''}" title="Other colour"><input type="color" value="${current || '#ffff00'}"></label>`;
  row.addEventListener('click', (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>('button');
    if (!b || b.disabled) return;
    if (b.dataset.color) o.onPick(b.dataset.color);
    else if (b.hasAttribute('data-none')) o.onPick(null);
  });
  const custom = row.querySelector<HTMLInputElement>('input[type="color"]')!;
  custom.addEventListener('input', () => o.onPreview?.(custom.value));
  custom.addEventListener('change', () => o.onPick(custom.value.toLowerCase()));
  return row;
}

/** Keeps a press inside a chooser from reaching the page; the system picker opens on its own click. */
export function holdPointer(node: HTMLElement): void {
  node.addEventListener('pointerdown', (ev) => {
    ev.stopPropagation();
    if (!(ev.target as Element).closest('label')) ev.preventDefault();
  });
}

let menu: HTMLDivElement | null = null;
export function closeColorMenu(): void { menu?.remove(); menu = null; }

/** A menu of swatch rows under `anchor` (a colour in the toolbar); closes when a colour is chosen. */
export function openColorMenu(anchor: HTMLElement, rows: SwatchRow[]): void {
  const reopen = menu?.dataset.for === anchor.id;
  closeColorMenu();
  if (reopen) return;   // a second click on the same colour closes it
  menu = document.createElement('div');
  menu.className = 'zc-pill zc-color-menu';
  menu.dataset.for = anchor.id;
  for (const r of rows) menu.appendChild(swatchRow({ ...r, onPick: (c) => { closeColorMenu(); r.onPick(c); } }));
  holdPointer(menu);
  document.body.appendChild(menu);
  const a = anchor.getBoundingClientRect(), m = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(a.left, innerWidth - m.width - 8))}px`;
  menu.style.top = `${a.bottom + 6}px`;
}
document.addEventListener('pointerdown', (e) => {
  if (menu && !menu.contains(e.target as Node) && !(e.target as Element).closest?.(`#${menu.dataset.for}`)) closeColorMenu();
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeColorMenu(); });
