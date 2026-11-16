// What a question is about, as the chip and the empty state show it.
import type { AskContext } from './client.js';

const snippet = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const flat = (s: string) => s.replace(/\s+/g, ' ').trim();

/** The chip's text: where the question comes from. */
export function contextLabel(c: AskContext): string {
  if (c.kind === 'note') {
    const name = c.path.split('/').pop()!.replace(/\.md$/i, '');
    return c.selection ? `${name} — “${snippet(flat(c.selection), 60)}”` : name;
  }
  const page = c.page ? `p. ${c.page}` : 'PDF';
  return c.quote ? `${page} — “${snippet(flat(c.quote), 60)}”` : page;
}

/** Things worth asking here, offered when the conversation is empty. */
export function suggestions(c: AskContext | undefined): string[] {
  if (c?.kind === 'note') return c.selection
    ? ['Explain this passage', 'Improve the wording', 'Which papers in my library are related?']
    : ['Summarise this note', 'List the open questions in this note', 'Which papers in my library are related?'];
  if (c?.kind === 'paper') return c.quote
    ? ['Explain this passage', 'Why does this matter for the paper?', 'Where else does the paper discuss this?']
    : ['Summarise this paper', 'What are the main results?', c.page ? 'Explain this page' : 'What are the limitations?'];
  return ['What is in my library about…', 'Summarise my recent notes'];
}
