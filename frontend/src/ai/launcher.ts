// The floating Ask AI button in the lower right corner: shown while Ask AI is set up (status.ts) and
// the panel is closed. The app has one for the whole window; the PDF viewer in a browser tab of its
// own has its own.
import { icon } from '../icons.js';
import { PANEL_EVENT, aiConfigured, onAiStatus, refreshAiStatus } from './status.js';

export function mountLauncher(open: () => void): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'ai-fab';
  b.title = 'Ask AI';
  b.setAttribute('aria-label', 'Ask AI');
  b.innerHTML = icon('sparkle');
  let panelOpen = false;
  const paint = () => { b.hidden = panelOpen || !aiConfigured(); };
  b.addEventListener('click', open);
  window.addEventListener(PANEL_EVENT, (e) => { panelOpen = !!(e as CustomEvent<boolean>).detail; paint(); });
  onAiStatus(paint);
  paint();
  document.body.append(b);
  void refreshAiStatus(60_000).then(paint);
  return b;
}
