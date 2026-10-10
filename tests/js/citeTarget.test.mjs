import assert from 'node:assert/strict';
import { test } from 'node:test';
import { citeTargetInText, quoteText, readRegion, viewerTarget, writeRegion } from '../../frontend/js/views/notes/citeTarget.js';
import { findQuote, normalise, parseRect } from '../../frontend/js/viewer/locate.js';

const note = `# Reading

> We find that transport is superdiffusive at
> infinite temperature [@ljubotina2023, p. 7]

Some text [@other, p. 2] here.

![ljubotina2023 p. 3](attachments/20260101-fig.png)
*Figure: [@ljubotina2023, p. 3]*
`;

test('a citation inside a block quote points at the quoted words', () => {
  const pos = note.indexOf('[@ljubotina2023, p. 7]') + 3;
  assert.deepEqual(citeTargetInText(note, pos), { quote: 'We find that transport is superdiffusive at infinite temperature' });
  // any line of the quote
  assert.deepEqual(citeTargetInText(note, note.indexOf('We find') + 2).quote.slice(0, 7), 'We find');
});

test('a figure caption points at the image above; other citations at nothing', () => {
  const pos = note.indexOf('*Figure: [@') + 12;
  assert.deepEqual(citeTargetInText(note, pos), { image: 'attachments/20260101-fig.png' });
  assert.equal(citeTargetInText(note, note.indexOf('[@other') + 2), null);
  // an image on the same line, before the citation
  const one = '![x](a.png) [@k, p. 1]';
  assert.deepEqual(citeTargetInText(one, one.indexOf('[@')), { image: 'a.png' });
  // not when a blank line separates them
  const apart = '![x](a.png)\n\n*Figure: [@k, p. 1]*';
  assert.equal(citeTargetInText(apart, apart.indexOf('[@')), null);
});

test('callout quotes and emphasis are cleaned', () => {
  const t = '> [!quote] From the paper\n> *really* important [@k, p. 4]';
  assert.deepEqual(citeTargetInText(t, t.length - 3), { quote: 'really important' });
  assert.equal(quoteText('a  [@x, p. 1]  b'), 'a b');
});

// smallest valid PNG: signature, IHDR (1x1), IDAT, IEND
const PNG = Uint8Array.from(Buffer.from('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c6360000200000500017a5eab3f0000000049454e44ae426082', 'hex'));

test('a snapshot PNG carries its region, and only ours is read', async () => {
  const region = { page: 3, rect: [0.1, 0.25, 0.5, 0.3] };
  const out = writeRegion(PNG, region);
  assert.equal(out.length > PNG.length, true);
  assert.deepEqual([...out.slice(-12)], [...PNG.slice(-12)]);     // still ends with IEND
  assert.deepEqual(readRegion(out), region);
  assert.equal(readRegion(PNG), null);
  assert.equal(readRegion(new Uint8Array([1, 2, 3])), null);
  const at = await viewerTarget({ image: 'attachments/x.png' }, { imageUrl: (p) => `/api/notes/${p}`, fetchBytes: async (src) => {
    assert.equal(src, '/api/notes/attachments/x.png'); return out;
  } });
  assert.deepEqual(at, { rect: '0.1,0.25,0.5,0.3', page: 3 });
  assert.deepEqual(await viewerTarget({ quote: 'hello' }), { q: 'hello' });
  assert.deepEqual(await viewerTarget({ image: 'gone.png' }, { fetchBytes: async () => { throw new Error('404'); } }), {});
  assert.deepEqual(await viewerTarget(null), {});
});

test('a quote is found on the page despite hyphenation, accents and ligatures', () => {
  const items = [
    { str: 'Introduction. We find that trans-', x: 50, y: 100, w: 330, h: 10 },
    { str: 'port is superdiffusive at in', x: 50, y: 114, w: 280, h: 10 },
    { str: 'ﬁnite temperature, as Bañuls showed.', x: 330, y: 114, w: 200, h: 10 },
    { str: 'Second column text', x: 600, y: 100, w: 150, h: 10 },
  ];
  const rects = findQuote(items, 'We find that transport is superdiffusive at infinite temperature');
  assert.equal(rects.length, 2);                     // two lines
  assert.ok(rects[0].x > 50 && rects[0].x < 200);   // starts at "We", not at the line start
  assert.equal(rects[0].y, 100);
  assert.ok(rects[1].x === 50 && rects[1].w > 280);  // the second line joins two items
  assert.equal(findQuote(items, 'not on this page at all, sorry about that, really not').length, 0);
  assert.equal(findQuote(items, 'banuls showed').length, 1);
  // a long quote that goes on to the next page: found from its beginning
  const long = 'We find that transport is superdiffusive at infinite temperature, as Bañuls showed. And this continues on the next page for a while';
  assert.ok(findQuote(items, long).length >= 2);
  assert.equal(normalise('Æ-ﬁ é').text, 'aefie');
});

test('rect parameter', () => {
  assert.deepEqual(parseRect('0.1,0.2,0.5,0.25', 600, 800), { x: 60, y: 160, w: 300, h: 200 });
  assert.equal(parseRect('1,2,3', 600, 800), null);
  assert.equal(parseRect('0,0,0,0', 600, 800), null);
});
