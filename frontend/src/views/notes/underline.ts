// Underline is ++text++: an explicit syntax, without enabling raw HTML in notes.
import type MarkdownIt from 'markdown-it';
import type { StateInline } from 'markdown-it';
import type { MarkdownConfig } from '@lezer/markdown';
import type { CM } from './editor.js';

export function underlineMarkdown(md: MarkdownIt): void {
  md.inline.ruler.before('emphasis', 'underline', (state: StateInline, silent: boolean) => {
    if (silent || state.src.charCodeAt(state.pos) !== 43) return false;
    const run = state.scanDelims(state.pos, true);
    const token = state.push('text', '', 0);
    token.content = '+'.repeat(run.length);
    if (run.length === 2) state.delimiters.push({
      marker: 43, length: 0, token: state.tokens.length - 1, end: -1,
      open: run.can_open, close: run.can_close,
    });
    state.pos += run.length;
    return true;
  });
  md.inline.ruler2.before('emphasis', 'underline', (state: StateInline) => {
    const process = (delimiters: StateInline['delimiters']) => {
      for (const start of delimiters) {
        if (start.marker !== 43 || start.end < 0) continue;
        const end = delimiters[start.end];
        const open = state.tokens[start.token], close = state.tokens[end.token];
        open.type = 'underline_open'; open.tag = 'u'; open.nesting = 1; open.content = '';
        close.type = 'underline_close'; close.tag = 'u'; close.nesting = -1; close.content = '';
      }
    };
    process(state.delimiters);
    for (const meta of state.tokens_meta) if (meta?.delimiters) process(meta.delimiters);
    return true;
  });
}

export function underlineLanguage(cm: CM): MarkdownConfig {
  const delimiter = { resolve: 'Underline', mark: 'UnderlineMark' };
  return {
    defineNodes: [
      { name: 'Underline', style: { 'Underline/...': cm.tags.special(cm.tags.emphasis) } },
      { name: 'UnderlineMark', style: cm.tags.processingInstruction },
    ],
    parseInline: [{
      name: 'Underline', after: 'Emphasis',
      parse(cx, next, pos) {
        if (next !== 43 || cx.char(pos + 1) !== 43 || cx.char(pos + 2) === 43 || cx.char(pos - 1) === 43) return -1;
        const before = cx.slice(pos - 1, pos), after = cx.slice(pos + 2, pos + 3);
        const sb = /\s|^$/.test(before), sa = /\s|^$/.test(after);
        const pb = /[\p{P}\p{S}]/u.test(before), pa = /[\p{P}\p{S}]/u.test(after);
        return cx.addDelimiter(delimiter, pos, pos + 2, !sa && (!pa || sb || pb), !sb && (!pb || sa || pa));
      },
    }],
  };
}
