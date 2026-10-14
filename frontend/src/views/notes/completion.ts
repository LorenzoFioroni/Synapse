// @ts-nocheck
// Autocomplete in the notes editor:
//   [@smi   or   @smi   → citation keys from the library ("@smi" alone becomes "[@key]")
//   [[Rev   or   ![[Rev → note names
import { completionContext, noteChoices, searchItems, searchNotes, shortAuthors } from './library.js';

const NO_COMPLETE = /^(InlineCode|CodeText|FencedCode|CodeBlock|CodeInfo|CodeMark|Frontmatter|URL|HTMLTag|Comment)$/;

/**
 * @param {object} cm
 * @param {{items(): object[], notes(): object[]}} data   cite index and notes overview
 */
export function completions(cm, data) {
  const inCode = (state, pos) => {
    for (let n = cm.syntaxTree(state).resolveInner(pos, -1); n; n = n.parent) if (NO_COMPLETE.test(n.name)) return true;
    return false;
  };

  function source(ctx) {
    const line = ctx.state.doc.lineAt(ctx.pos);
    const before = line.text.slice(0, ctx.pos - line.from);
    const c = completionContext(before);
    if (!c || inCode(ctx.state, ctx.pos)) return null;
    if (!ctx.explicit && c.kind === 'cite' && !c.bracket && !c.query) return null;   // a lone "@": wait for a letter
    const from = line.from + c.from;

    if (c.kind === 'note') {
      const after = ctx.state.sliceDoc(ctx.pos, ctx.pos + 2);
      return {
        from,
        filter: false,
        options: searchNotes(noteChoices(data.notes()), c.query).map((n) => ({
          label: n.insert,
          detail: n.title !== n.stem ? n.title : n.folder,
          type: 'note',
          apply(view, _c, f, t) {
            const close = after === ']]' ? '' : ']]';
            const insert = n.insert + close;
            view.dispatch({ changes: { from: f, to: t, insert }, selection: { anchor: f + n.insert.length + 2 } });
          },
        })),
      };
    }

    return {
      from,
      filter: false,
      options: searchItems(data.items(), c.query).map((it) => ({
        label: it.key,
        detail: [shortAuthors(it.authors), it.year].filter(Boolean).join(' '),
        info: it.title || undefined,
        type: 'cite',
        apply(view, _c, f, t) {
          if (c.bracket) {
            view.dispatch({ changes: { from: f, to: t, insert: it.key }, selection: { anchor: f + it.key.length } });
            return;
          }
          // bare "@query" → "[@key]" (only the bracketed form is a citation)
          const at = f - 1;
          const dash = view.state.sliceDoc(at - 1, at) === '-' ? 1 : 0;
          const insert = `[${dash ? '-' : ''}@${it.key}]`;
          view.dispatch({ changes: { from: at - dash, to: t, insert }, selection: { anchor: at - dash + insert.length } });
        },
      })),
    };
  }

  return cm.autocompletion({ override: [source], icons: false, activateOnTyping: true, maxRenderedOptions: 30 });
}
