// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { moveMessage } from '../../frontend/js/views/notes/linkReport.js';

test('a move says what it did to links, and what it could not do', () => {
  assert.equal(moveMessage({ updated_links: [], broken_links: [] }), '');
  assert.equal(moveMessage({ updated_links: [{ path: 'a/One.md', count: 1 }], broken_links: [] }), 'Updated 1 link in 1 note.');
  assert.equal(moveMessage({ updated_links: [{ path: 'One.md', count: 2 }, { path: 'x/Two.md', count: 3 }], broken_links: [] }),
    'Updated 5 links in 2 notes.');
  assert.equal(moveMessage({ updated_links: [], broken_links: [{ path: 'x/Busy.md', line: 3 }] }),
    '1 link in Busy could not be updated and no longer resolves.');
  const four = ['A', 'B', 'C', 'D'].map((n) => ({ path: `${n}.md`, line: 1 }));
  assert.equal(moveMessage({ updated_links: [{ path: 'One.md', count: 1 }], broken_links: four }),
    'Updated 1 link in 1 note. 4 links in A, B, C… could not be updated and no longer resolve.');
});
