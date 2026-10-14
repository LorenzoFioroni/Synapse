// The tools' colours and sizes, and the viewer's preferences from Settings (PDF Viewer). See
// annotations.ts for the overview.
//
// Each tool starts with the colour and size it was last used with on this device (localStorage).
// A colour is remembered with the default it was chosen over, so changing a default in Settings
// takes effect on every device, until another colour is chosen again. The box tool also has a
// fill colour, and either its border or its fill can be left out (not both); a part left out is
// marked with data-none on its colour in the toolbar, which model.ts reads.
import { closeColorMenu, openColorMenu } from './colorMenu.js';
import { api, tool } from './model.js';

/** What a tool starts with when nothing is remembered and Settings has no colour for it. */
const BUILT_IN: Record<string, { color: string; size?: number; fill?: string }> = {
  highlight: { color: '#ffff00' },
  underline: { color: '#ff0000', size: 2 },
  text: { color: '#000000', size: 16 },
  box: { color: '#000000', size: 2, fill: '#ffff00' },
  draw: { color: '#000000', size: 2 },
};

export const prefs = {
  /** Highlights and underlines take whole words (Alt / ⌥ while selecting takes exact characters). */
  snapWords: true,
  /** Default colour of each tool, from Settings. */
  colors: {} as Record<string, string>,
  /** Default fill of a box, from Settings. */
  boxFill: '',
};

interface Remembered {
  color?: string; base?: string; size?: number;
  fill?: string; fillBase?: string;
  /** box: the border is left out / the box is filled */
  noBorder?: boolean; filled?: boolean;
}
const KEY = 'zc.toolStyle';
function remembered(): Record<string, Remembered> {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { return {}; }
}
function remember(t: string, part: Remembered): void {
  const all = remembered();
  all[t] = { ...all[t], ...part };
  try { localStorage.setItem(KEY, JSON.stringify(all)); } catch { /* private mode */ }
}

const defaultColor = (t: string) => prefs.colors[t] || BUILT_IN[t].color;
const defaultFill = () => prefs.boxFill || BUILT_IN.box.fill!;
const byId = <T extends HTMLElement>(id: string) => document.getElementById(id) as T | null;
const colorInput = () => byId<HTMLInputElement>('tool-color');
const fillInput = () => byId<HTMLInputElement>('tool-fill');
const sizeInput = () => byId<HTMLInputElement>('tool-size');
const colorWell = () => byId<HTMLElement>('color-wrapper');
const fillWell = () => byId<HTMLElement>('fill-wrapper');

/** Sets the toolbar's colours and size for tool t. */
function applyTool(t: string): void {
  closeColorMenu();
  colorWell()?.removeAttribute('data-none');
  if (!BUILT_IN[t]) return;
  const mem = remembered()[t] || {};
  const c = colorInput(), s = sizeInput(), f = fillInput();
  if (c) c.value = mem.color && mem.base === defaultColor(t) ? mem.color : defaultColor(t);
  const size = mem.size ?? BUILT_IN[t].size;
  if (s && size) s.value = String(size);
  if (t === 'box') {
    if (f) f.value = mem.fill && mem.fillBase === defaultFill() ? mem.fill : defaultFill();
    colorWell()?.toggleAttribute('data-none', !!mem.noBorder && !!mem.filled);
    fillWell()?.toggleAttribute('data-none', !mem.filled);
  }
}

/** The toolbar's colour (or fill) chosen; null leaves the box's border (or fill) out. */
function pick(part: 'color' | 'fill', c: string | null): void {
  const t = tool();
  if (!BUILT_IN[t]) return;
  const well = part === 'color' ? colorWell() : fillWell();
  well?.toggleAttribute('data-none', c === null);
  if (part === 'color') {
    if (c) colorInput()!.value = c;
    remember(t, c ? { color: c, base: defaultColor(t), noBorder: false } : { noBorder: true });
  } else {
    if (c) fillInput()!.value = c;
    remember(t, c ? { fill: c, fillBase: defaultFill(), filled: true } : { filled: false });
  }
}

/** The menu of a colour in the toolbar; for a box, the border or the fill may be left out (not both). */
function openWell(part: 'color' | 'fill'): void {
  const well = part === 'color' ? colorWell() : fillWell();
  const input = part === 'color' ? colorInput() : fillInput();
  if (!well || !input) return;
  const box = tool() === 'box';
  const other = part === 'color' ? fillWell() : colorWell();
  openColorMenu(well, [{
    label: box ? (part === 'color' ? 'Border' : 'Fill') : undefined,
    value: well.hasAttribute('data-none') ? null : input.value,
    none: box ? (other?.hasAttribute('data-none') ? 'disabled' : true) : false,
    onPick: (c) => pick(part, c),
  }]);
}

async function loadSettings(): Promise<void> {
  try {
    const { settings } = await api('GET', '/api/settings');
    prefs.snapWords = settings.annotation_snap_words !== false;
    const ok = (c: unknown): c is string => typeof c === 'string' && /^#[0-9a-f]{6}$/i.test(c);
    for (const t of Object.keys(BUILT_IN)) {
      const c = settings[`annotation_color_${t}`];
      if (ok(c)) prefs.colors[t] = c.toLowerCase();
    }
    if (ok(settings.annotation_fill_box)) prefs.boxFill = settings.annotation_fill_box.toLowerCase();
  } catch { /* keep the built-in defaults */ }
}

document.addEventListener('zc:toolchange', (e) => applyTool(e.detail.tool));
colorWell()?.addEventListener('click', () => openWell('color'));
fillWell()?.addEventListener('click', () => openWell('fill'));
// the colour set by other means (the browser checklists)
colorInput()?.addEventListener('input', () => pick('color', colorInput()!.value.toLowerCase()));
sizeInput()?.addEventListener('change', () => {
  const t = tool(), v = Number(sizeInput()!.value);
  if (BUILT_IN[t]?.size && v > 0) remember(t, { size: v });
});
// Settings may have changed in the app's tab meanwhile
window.addEventListener('focus', loadSettings);
export const prefsReady = loadSettings();
