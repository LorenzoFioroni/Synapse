// Spoilers use generated markup only; user HTML stays disabled in the shared renderer.
import type MarkdownIt from 'markdown-it';
import { inlineEnd } from './syntax.js';

export function installSpoilers(md: MarkdownIt): void {
  // markdown-it treats pipes as ordinary text outside tables. Stop its text rule at our
  // delimiter while letting its existing escape, emphasis and code rules keep their precedence.
  const textRule = md.inline.ruler.getRules('')[0];
  md.inline.ruler.at('text', (state, silent) => {
    const max = state.posMax;
    const next = state.src.indexOf('||', state.pos + 1);
    if (next >= 0) state.posMax = Math.min(max, next);
    try { return textRule(state, silent); } finally { state.posMax = max; }
  });
  md.inline.ruler.before('text', 'spoiler', (state, silent) => {
    if (!state.src.startsWith('||', state.pos)) return false;
    const end = inlineEnd(state.src.slice(0, state.posMax), state.pos);
    if (end < 0) return false;
    if (!silent) {
      state.push('spoiler_open', 'span', 1).attrSet('class', 'spoiler-inline');
      state.push('spoiler_toggle', '', 0);
      const content = state.push('spoiler_content_open', 'span', 1);
      content.attrSet('class', 'spoiler-content'); content.attrSet('hidden', '');
      const children: typeof state.tokens = [];
      state.md.inline.parse(state.src.slice(state.pos + 2, end - 2), state.md, state.env, children);
      state.tokens.push(...children);
      state.push('spoiler_content_close', 'span', -1);
      state.push('spoiler_close', 'span', -1);
    }
    state.pos = end;
    return true;
  });
  md.renderer.rules.spoiler_toggle = () => '<button type="button" class="spoiler-toggle" aria-expanded="false">Show spoiler</button>';
  md.block.ruler.before('fence', 'spoiler_block', (state, start, end, silent) => {
    const text = (line: number) => state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]);
    if (state.sCount[start] - state.blkIndent >= 4) return false;
    const match = /^:::spoiler(?:[ \t]+(.*))?$/.exec(text(start));
    if (!match) return false;
    let close = start + 1, depth = 1, fence = '';
    for (; close < end; close++) {
      const line = text(close);
      const code = /^(`{3,}|~{3,})/.exec(line);
      if (code) {
        if (!fence) fence = code[1];
        else if (code[1][0] === fence[0] && code[1].length >= fence.length && !line.slice(code[1].length).trim()) fence = '';
        continue;
      }
      if (fence) continue;
      if (/^:::spoiler(?:[ \t]+.*)?$/.test(line)) depth++;
      if (/^:::[ \t]*$/.test(line) && --depth === 0) break;
    }
    if (close === end) return false;
    if (silent) return true;
    const parent = state.parentType, max = state.lineMax;
    const token = state.push('spoiler_block_open', 'details', 1);
    token.block = true; token.map = [start, close + 1]; token.attrSet('class', 'spoiler-block');
    state.push('spoiler_summary_open', 'summary', 1);
    const title = state.push('inline', '', 0); title.content = match[1]?.trim() || 'Spoiler'; title.children = [];
    state.push('spoiler_summary_close', 'summary', -1);
    state.parentType = 'blockquote'; state.lineMax = close;
    state.md.block.tokenize(state, start + 1, close);
    state.parentType = parent; state.lineMax = max;
    state.push('spoiler_block_close', 'details', -1).block = true;
    state.line = close + 1;
    return true;
  }, { alt: ['paragraph', 'reference', 'blockquote', 'list'] });
}
