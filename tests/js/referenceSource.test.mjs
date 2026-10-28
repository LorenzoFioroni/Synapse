import { test } from 'node:test';
import assert from 'node:assert/strict';
import { referenceSourceTitle } from '../../frontend/js/views/referenceSource.js';

test('reference source titles support plugin declarations, old servers and unknown saved providers', () => {
  assert.equal(referenceSourceTitle({ source: 'local.catalogue.index', source_title: 'My index' }), 'My index');
  assert.equal(referenceSourceTitle({ source: 'semanticscholar' }), 'Semantic Scholar');
  assert.equal(referenceSourceTitle({ source: 'arxiv-html' }), 'arXiv (HTML version)');
  assert.equal(referenceSourceTitle({ source: 'unknown.saved.provider' }), 'unknown.saved.provider');
});
