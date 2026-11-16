// What a question is about: the notes, papers and passages added to it, as chips. The page offers
// what is on screen (a host's ``here``) as dashed chips that add it with one click.
import { icon } from '../icons.js';
import type { AskContext } from './client.js';

/** Context as a chip shows it. ``label`` and ``title`` are set as text, never as HTML. */
export interface Attached { ctx: AskContext; label: string; title: string; icon: string }

/** At most this many go with one question (backend/companion/prompt.py MAX_CONTEXTS). */
export const MAX_ATTACHED = 8;

const snippet = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const flat = (s: string) => s.replace(/\s+/g, ' ').trim();

export const contextKey = (c: AskContext): string => JSON.stringify(c);

/** The chip for ``c``; ``paperName`` is the item's citation key or title when known. */
export function describe(c: AskContext, paperName?: string): Attached {
  if (c.kind === 'note') {
    const name = c.path.split('/').pop()!.replace(/\.md$/i, '');
    if (!c.selection) return { ctx: c, label: name, title: `Note: ${c.path}`, icon: 'doc' };
    const words = flat(c.selection);
    return { ctx: c, label: `“${snippet(words, 40)}”`, title: `${name}: “${snippet(words, 300)}”`, icon: 'text' };
  }
  const where = [snippet(flat(paperName || 'Paper'), 40), c.page ? `p. ${c.page}` : ''].filter(Boolean).join(', ');
  if (!c.quote) return { ctx: c, label: where, title: c.page ? `${where} (the paper, open at this page)` : where, icon: 'pdf' };
  const words = flat(c.quote);
  return { ctx: c, label: `“${snippet(words, 40)}”`, title: `${where}: “${snippet(words, 300)}”`, icon: 'text' };
}

/** What the empty conversation is titled. */
export function emptyTitle(attached: Attached[]): string {
  if (attached.length > 1) return 'Ask about these';
  const c = attached[0]?.ctx;
  return c?.kind === 'note' ? 'Ask about this note' : c?.kind === 'paper' ? 'Ask about this paper' : 'Ask about your library';
}

/** Things worth asking, offered when the conversation is empty. */
export function suggestions(attached: Attached[]): string[] {
  if (attached.length > 1) return ['How do these relate?', 'Compare them', 'Summarise them together'];
  const c = attached[0]?.ctx;
  if (c?.kind === 'note') return c.selection
    ? ['Explain this passage', 'Improve the wording', 'Which papers in my library are related?']
    : ['Summarise this note', 'List the open questions in this note', 'Which papers in my library are related?'];
  if (c?.kind === 'paper') return c.quote
    ? ['Explain this passage', 'Why does this matter for the paper?', 'Where else does the paper discuss this?']
    : ['Summarise this paper', 'What are the main results?', c.page ? 'Explain this page' : 'What are the limitations?'];
  return ['What is in my library about…', 'Summarise my recent notes'];
}

/** A context chip: its icon and label (as text). */
export function contextChip(a: Attached, tag: 'span' | 'button', glyph = a.icon): HTMLElement {
  const chip = document.createElement(tag);
  chip.className = 'ai-chip';
  if (chip instanceof HTMLButtonElement) chip.type = 'button';
  chip.title = a.title;
  chip.innerHTML = icon(glyph, 'icon-sm');
  const text = document.createElement('span');
  text.className = 'ai-chip-text';
  text.textContent = a.label;
  chip.append(text);
  return chip;
}
