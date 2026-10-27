// The host owns registrations; changing providers clears pending and visible ghost text.
import type { SuggestionSource } from './api.js';
import { setNoteHook } from '../views/notes/hooks.js';

const sources = new Map<symbol, SuggestionSource>();
function changed(): void {
  setNoteHook('ghost', [...sources.values()].at(-1) ?? null);
}

export function registerSuggestions(source: SuggestionSource): () => void {
  const key = Symbol();
  sources.set(key, source);
  changed();
  return () => { if (sources.delete(key)) changed(); };
}
