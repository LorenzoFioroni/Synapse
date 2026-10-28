// Compose public Markdown contributions; the host owns rebuilds and registration disposal.
import { noteHooks, setNoteHook, type ExtendedMarkdownHook, type FiguresHook } from '../views/notes/hooks.js';
const providers = new Map<symbol, ExtendedMarkdownHook>();
function sync(): void {
  const current = [...providers.values()];
  setNoteHook('extended', current.length ? {
    markdown: md => { for (const p of current) p.markdown(md); },
    spoilers: (src, scanned) => current.flatMap(p => p.spoilers(src, scanned)).sort((a, b) => a.from - b.from),
    exportStatic: root => { for (const p of current) p.exportStatic(root); },
    fence: info => current.some(p => p.fence(info)),
    portable: text => current.reduce((src, p) => p.portable(src), text),
  } : null);
}
export function registerMarkdown(provider: ExtendedMarkdownHook): () => void {
  const key = Symbol(); providers.set(key, provider); sync();
  return () => { if (providers.delete(key)) sync(); };
}
export function registerFigures(provider: FiguresHook): () => void {
  if (noteHooks.figures) throw new Error('A figure provider is already registered');
  setNoteHook('figures', provider);
  return () => { if (noteHooks.figures === provider) setNoteHook('figures', null); };
}
