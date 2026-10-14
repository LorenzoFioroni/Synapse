// The tools' colours and sizes, and the viewer's preferences from Settings (PDF Viewer). See
// annotations.ts for the overview.
//
// Each tool starts with the colour and size it was last used with on this device (localStorage).
// The colour is remembered with the default it was chosen over, so changing a default in Settings
// takes effect on every device, until another colour is chosen again.
import { api, tool } from './model.js';

/** What a tool starts with when nothing is remembered and Settings has no colour for it. */
const BUILT_IN: Record<string, { color: string; size?: number }> = {
  highlight: { color: '#ffff00' },
  underline: { color: '#ff0000', size: 2 },
  text: { color: '#000000', size: 16 },
  box: { color: '#000000', size: 2 },
  draw: { color: '#000000', size: 2 },
};
/** The tool whose colour and size a tool uses: an area highlight looks like a text highlight. */
const styleOf = (t: string) => (t === 'area' ? 'highlight' : t);

export const prefs = {
  /** Highlights and underlines take whole words (Alt / ⌥ while selecting takes exact characters). */
  snapWords: true,
  /** Default colour of each tool, from Settings. */
  colors: {} as Record<string, string>,
};

interface Remembered { color?: string; base?: string; size?: number }
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
const colorInput = () => document.getElementById('tool-color') as HTMLInputElement | null;
const sizeInput = () => document.getElementById('tool-size') as HTMLInputElement | null;

/** Sets the toolbar's colour and size for tool t. */
function applyTool(t: string): void {
  const k = styleOf(t);
  if (!BUILT_IN[k]) return;
  const mem = remembered()[k] || {};
  const c = colorInput(), s = sizeInput();
  if (c) c.value = mem.color && mem.base === defaultColor(k) ? mem.color : defaultColor(k);
  const size = mem.size ?? BUILT_IN[k].size;
  if (s && size) s.value = String(size);
}

async function loadSettings(): Promise<void> {
  try {
    const { settings } = await api('GET', '/api/settings');
    prefs.snapWords = settings.annotation_snap_words !== false;
    for (const t of Object.keys(BUILT_IN)) {
      const c = settings[`annotation_color_${t}`];
      if (typeof c === 'string' && /^#[0-9a-f]{6}$/i.test(c)) prefs.colors[t] = c.toLowerCase();
    }
  } catch { /* keep the built-in defaults */ }
}

document.addEventListener('zc:toolchange', (e) => applyTool(e.detail.tool));
colorInput()?.addEventListener('input', () => {
  const k = styleOf(tool());
  if (BUILT_IN[k]) remember(k, { color: colorInput()!.value.toLowerCase(), base: defaultColor(k) });
});
sizeInput()?.addEventListener('change', () => {
  const k = styleOf(tool()), v = Number(sizeInput()!.value);
  if (BUILT_IN[k]?.size && v > 0) remember(k, { size: v });
});
// Settings may have changed in the app's tab meanwhile
window.addEventListener('focus', loadSettings);
export const prefsReady = loadSettings();
