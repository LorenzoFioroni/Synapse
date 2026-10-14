// extractCitation runs in the page, so it reads the globals `document` and `location`: fake them.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { extractCitation } from '../../browser_extension/extract.js';

/** @param {string} href  @param {{ contentType?: string, title?: string }} doc */
function onPage(href, doc = {}) {
  const u = new URL(href);
  globalThis.location = { href, hostname: u.hostname, pathname: u.pathname };
  globalThis.document = {
    contentType: doc.contentType || 'text/html', title: doc.title || '',
    querySelector: () => null, querySelectorAll: () => [],
  };
}

test('a PDF whose URL has malformed %-escapes is still read', () => {
  onPage('https://example.org/files/10.1234/abc%E0%A4%A/report%ZZ.pdf', { contentType: 'application/pdf' });
  const c = extractCitation();
  assert.equal(c.is_pdf, true);
  assert.equal(c.title, 'report%ZZ.pdf');
  assert.equal(c.doi, '10.1234/abc%E0%A4%A/report%ZZ.pdf');
});

test('a well-formed escaped DOI in a page URL is decoded', () => {
  onPage('https://example.org/doi/10.1234%2Fxyz.5');
  assert.equal(extractCitation().doi, '10.1234/xyz.5');
});
