// Where exactly a citation points inside the PDF (issue #8).
//
// A quote made with "Quote to note" ends with its citation:  > the quoted words [@key, p. 7]
// so the quoted words themselves say where on the page to look. A snapshot made with
// "Snapshot to note" is an image followed by its citation:   ![key p. 7](attachments/x.png)
//                                                            *Figure: [@key, p. 7]*
// and the PNG carries the region it was cut from (a tEXt chunk, see writeRegion), so nothing
// extra is written into the note. The viewer then scrolls to that spot and flashes it.
//
// No DOM access in the text and PNG helpers, so they are unit-tested with Node.

const QUOTE_MAX = 400;
const CITES = /\[[^\[\]]*@[^\[\]]*\]/g;
const IMAGE = /!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/;

const lineBounds = (text, pos) => {
  const from = text.lastIndexOf('\n', pos - 1) + 1;
  let to = text.indexOf('\n', pos);
  if (to < 0) to = text.length;
  return [from, to];
};

/** Plain text of quoted words: no citations, no markdown emphasis, one line. */
export function quoteText(s) {
  return s.replace(CITES, ' ').replace(/\(\s*\)/g, ' ')    // "()" left where a rendered citation was
    .replace(/[*_`]+/g, '').replace(/\s+/g, ' ').trim().slice(0, QUOTE_MAX);
}

/**
 * The target of the citation at `pos` in a note's text: {quote} when it is inside a block
 * quote, {image} (the image path) when it is the caption of an image, else null.
 */
export function citeTargetInText(text, pos) {
  const [from, to] = lineBounds(text, pos);
  const line = text.slice(from, to);
  if (/^\s{0,3}>/.test(line)) {
    // the whole block quote: neighbouring lines that also start with ">"
    const lines = text.split('\n');
    let n = text.slice(0, from).split('\n').length - 1;
    let a = n, b = n;
    while (a > 0 && /^\s{0,3}>/.test(lines[a - 1])) a--;
    while (b < lines.length - 1 && /^\s{0,3}>/.test(lines[b + 1])) b++;
    const body = lines.slice(a, b + 1)
      .map((l) => l.replace(/^\s{0,3}(?:>\s?)+/, ''))
      .filter((l) => !/^\[![a-z]+\]/i.test(l));     // a callout's title line
    const quote = quoteText(body.join(' '));
    return quote ? { quote } : null;
  }
  // an image on this line before the citation, or on the line just above (same paragraph)
  const before = line.slice(0, pos - from);
  let m = IMAGE.exec(before);
  if (!m && from > 0) {
    const [pf, pt] = lineBounds(text, from - 1);
    const prev = text.slice(pf, pt);
    if (prev.trim()) m = IMAGE.exec(prev);
  }
  return m ? { image: m[1] } : null;
}

/** The same, for a citation shown in read mode (rendered HTML). */
export function citeTargetInDom(el) {
  const bq = el.closest?.('blockquote');
  if (bq) {
    const copy = bq.cloneNode(true);
    copy.querySelectorAll('.cite, .callout-title, .callout-title-inner').forEach((c) => c.remove());
    const quote = quoteText(copy.textContent || '');
    return quote ? { quote } : null;
  }
  const img = el.closest?.('p, figure, li')?.querySelector('img');
  return img ? { imageSrc: img.getAttribute('src') } : null;
}

/* ---------------------------------------------------------------- PNG region */
export const REGION_KEYWORD = 'zc:pdf-region';

const CRC = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(bytes) {
  let c = 0xffffffff;
  for (const b of bytes) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
const isPng = (b) => b.length > 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47;

/**
 * A copy of the PNG `bytes` (Uint8Array) carrying `region` ({page, rect: [x, y, w, h]} as
 * fractions of the page) in a tEXt chunk, just before IEND.
 */
export function writeRegion(bytes, region) {
  if (!isPng(bytes)) return bytes;
  const enc = new TextEncoder();
  const data = enc.encode(`${REGION_KEYWORD}\0${JSON.stringify(region)}`);
  const type = enc.encode('tEXt');
  const chunk = new Uint8Array(12 + data.length);
  const dv = new DataView(chunk.buffer);
  dv.setUint32(0, data.length);
  chunk.set(type, 4);
  chunk.set(data, 8);
  dv.setUint32(8 + data.length, crc32(chunk.subarray(4, 8 + data.length)));
  const iend = bytes.length - 12;     // IEND is always the last, empty chunk
  const out = new Uint8Array(bytes.length + chunk.length);
  out.set(bytes.subarray(0, iend), 0);
  out.set(chunk, iend);
  out.set(bytes.subarray(iend), iend + chunk.length);
  return out;
}

/** The region stored by writeRegion, or null. */
export function readRegion(bytes) {
  if (!isPng(bytes)) return null;
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const dec = new TextDecoder();
  for (let i = 8; i + 12 <= bytes.length;) {
    const len = dv.getUint32(i);
    const type = dec.decode(bytes.subarray(i + 4, i + 8));
    if (type === 'tEXt') {
      const data = bytes.subarray(i + 8, i + 8 + len);
      const z = data.indexOf(0);
      if (z > 0 && dec.decode(data.subarray(0, z)) === REGION_KEYWORD) {
        try {
          const r = JSON.parse(dec.decode(data.subarray(z + 1)));
          if (Array.isArray(r.rect) && r.rect.length === 4 && r.rect.every((v) => typeof v === 'number')) return r;
        } catch { /* not ours */ }
      }
    }
    if (type === 'IEND') break;
    i += 12 + len;
  }
  return null;
}

/* --------------------------------------------------------------- resolution */
/**
 * Turn a target into what the viewer understands: {q} (words to find on the page) or
 * {rect: "x,y,w,h"} (a region of the page). `fetchBytes(src)` is injectable for tests.
 */
export async function viewerTarget(target, { imageUrl = (p) => p, fetchBytes = defaultFetch } = {}) {
  if (!target) return {};
  if (target.quote) return { q: target.quote };
  const src = target.imageSrc || (target.image && imageUrl(target.image));
  if (!src) return {};
  try {
    const region = readRegion(await fetchBytes(src));
    if (region) return { rect: region.rect.map((v) => +v.toFixed(4)).join(','), page: region.page };
  } catch { /* the image is gone or not a snapshot: just the page */ }
  return {};
}

async function defaultFetch(src) {
  const r = await fetch(src);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return new Uint8Array(await r.arrayBuffer());
}
