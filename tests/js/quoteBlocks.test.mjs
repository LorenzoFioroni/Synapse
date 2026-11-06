// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
// Live preview must draw a table inside a quote only where the read view (markdown-it) does.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as cm from '../../frontend/vendor/cm6/cm6.js';
import * as lib from '../../frontend/vendor/md/md.js';
import { quoteEnd, quotedTableEnd, tableSource } from '../../frontend/js/views/notes/quoteBlocks.js';

const md = new lib.MarkdownIt();

function parse(src) {
  const state = cm.EditorState.create({ doc: src, extensions: [cm.markdown({ base: cm.markdownLanguage })] });
  const nodes = { Table: [], Blockquote: [] };
  cm.syntaxTree(state).iterate({ enter(n) { if (n.name in nodes) nodes[n.name].push(n.node); } });
  return { doc: state.doc, ...nodes };
}

// the lines of the table drawn by live preview, or null when none is drawn
function drawn(src) {
  const { doc, Table } = parse(src);
  assert.equal(Table.length, 1, 'CodeMirror sees one table');
  const end = quotedTableEnd(doc, Table[0]);
  return end === null ? null : doc.lineAt(end).number - doc.lineAt(Table[0].from).number + 1;
}

// the lines of the table drawn by the read view (header, delimiter and rows), or null
function rendered(src) {
  const tokens = md.parse(src, {});
  const table = tokens.find((t) => t.type === 'table_open');
  return table ? table.map[1] - table.map[0] : null;
}

const CASES = {
  'every line quoted': '>| a | b |\n>|---|---|\n>| 1 | 2 |\n>| 3 | 4 |',
  'delimiter without ">"': '> text\n>| a | b |\n|---|---|\n| 1 | 2 |\n>\n> after',
  'rows without ">"': '>| a | b |\n>|---|---|\n| 1 | 2 |\n| 3 | 4 |',
  'last row without ">"': '>| a | b |\n>|---|---|\n>| 1 | 2 |\n| 3 | 4 |',
  'nested, one ">" short': '> > | a | b |\n> |---|---|\n> > | 1 | 2 |',
  'nested, all quoted': '> > | a | b |\n> > |---|---|\n> > | 1 | 2 |',
  'in a list in a quote': '> - item\n>   | a | b |\n>   |---|---|\n>   | 1 | 2 |',
  'not in a quote': '| a | b |\n|---|---|\n| 1 | 2 |',
};

for (const [name, src] of Object.entries(CASES)) {
  test(`table lines agree with the read view: ${name}`, () => {
    assert.equal(drawn(src), rendered(src));
  });
}

test('a callout table missing ">" on its delimiter is not drawn and the callout goes on', () => {
  const src = '>[!note] How cold?\n>In the table below:\n>| $N$ | $P$ |\n|---|---|\n| 8 | 0.07 |\n>\n>After.';
  const { doc, Table, Blockquote } = parse(src);
  assert.equal(quotedTableEnd(doc, Table[0]), null);
  assert.equal(quoteEnd(doc, Blockquote[0]), Blockquote[0].to);
});

test('a row without ">" ends the table and the quote box', () => {
  const src = '> [!note] T\n>| a | b |\n>|---|---|\n>| 1 | 2 |\n| 3 | 4 |';
  const { doc, Table, Blockquote } = parse(src);
  const end = quotedTableEnd(doc, Table[0]);
  assert.equal(doc.lineAt(end).number, 4);
  assert.equal(quoteEnd(doc, Blockquote[0]), end);
  assert.equal(tableSource(doc, Table[0], end), '| a | b |\n|---|---|\n| 1 | 2 |');
});

test('the whole quoted table is passed to the renderer without its markers', () => {
  const { doc, Table } = parse('> > | a | b |\n> > |---|---|\n> > | 1 | 2 |');
  assert.equal(tableSource(doc, Table[0]), '| a | b |\n|---|---|\n| 1 | 2 |');
});
