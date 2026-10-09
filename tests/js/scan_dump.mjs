// Helper for tests/notes/test_scanner_parity.py: reads a JSON list of texts on stdin and
// prints what frontend/js/views/notes/mdscan.js finds in each.
import { mask, scan, splitFrontmatter } from '../../frontend/js/views/notes/mdscan.js';

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', () => {
  const out = JSON.parse(input).map((text) => {
    const t = text.replace(/\r\n?/g, '\n');
    const fm = splitFrontmatter(t);
    const r = scan(t);
    return {
      masked: mask(t.slice(fm.bodyStart)).masked,
      links: r.links.map((l) => ({ kind: l.kind, target: l.target, line: l.line, heading: l.heading, alias: l.alias,
        locator: l.locator, page: l.page, prefix: l.prefix, suppress_author: l.suppress_author, group: l.group })),
      headings: r.headings.map((h) => [h.level, h.text, h.line]),
      tags: [...new Set(r.tags.map((x) => x.tag))],
    };
  });
  process.stdout.write(JSON.stringify(out));
});
