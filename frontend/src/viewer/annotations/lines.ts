// Geometry of a text selection, without the DOM (unit-tested in tests/js/annotationLines.test.mjs):
// the boxes of the selected text merged into one box per line, and where a word starts and ends.

/** A box in any unit, origin top left. */
export interface Box { left: number; top: number; right: number; bottom: number }

/** The value below which half of the total weight lies. */
function weightedMedian(pairs: [number, number][]): number {
  pairs.sort((a, b) => a[0] - b[0]);
  const total = pairs.reduce((s, [, w]) => s + w, 0);
  let acc = 0;
  for (const [v, w] of pairs) {
    acc += w;
    if (acc >= total / 2) return v;
  }
  return pairs[pairs.length - 1][0];
}

/** The band (top and bottom) most of the line's text sits in: inline math, sub- and superscripts
    are narrow and do not move it. */
function band(boxes: Box[]): { top: number; bottom: number } {
  const w = (b: Box) => Math.max(b.right - b.left, 0.01);
  return {
    top: weightedMedian(boxes.map((b) => [b.top, w(b)])),
    bottom: weightedMedian(boxes.map((b) => [b.bottom, w(b)])),
  };
}

/** The boxes of selected text, merged into one box per line.
    The text layer gives a box per run of text, and inline math, sub- and superscripts sit higher
    or lower than the words around them: drawn as they are, an underline breaks at every formula.
    Boxes belong to one line when their middles are close (within 0.65 of the taller height, while
    the next line is about 1.2 heights away) and they are no more than a height apart sideways (so
    two columns of text stay apart). Each line spans its boxes from left to right, at the height
    of the text most of it is made of. */
export function mergeLines(boxes: Box[]): Box[] {
  const lines: { boxes: Box[]; left: number; right: number; top: number; bottom: number }[] = [];
  for (const b of boxes) {
    const h = b.bottom - b.top;
    const mid = (b.top + b.bottom) / 2;
    const line = lines.find((l) => {
      const lh = l.bottom - l.top;
      const tall = Math.max(lh, h);
      return Math.abs(mid - (l.top + l.bottom) / 2) < 0.65 * tall
        && b.left <= l.right + tall && b.right >= l.left - tall;
    });
    if (!line) {
      lines.push({ boxes: [b], left: b.left, right: b.right, top: b.top, bottom: b.bottom });
      continue;
    }
    line.boxes.push(b);
    line.left = Math.min(line.left, b.left);
    line.right = Math.max(line.right, b.right);
    Object.assign(line, band(line.boxes));
  }
  return lines.map(({ left, right, top, bottom }) => ({ left, top, right, bottom }));
}

const WORD = /[\p{L}\p{N}\p{M}_'’]/u;
const SPACE = /\s/;

/** The start of a selection moved to the start of its word, past any spaces it begins with.
    `limit` is where the selection ends when it ends in the same text. */
export function wordStart(text: string, i: number, limit = text.length): number {
  while (i < limit && SPACE.test(text[i])) i++;
  if (i < text.length && WORD.test(text[i])) while (i > 0 && WORD.test(text[i - 1])) i--;
  return i;
}

/** The end of a selection moved to the end of its word, before any spaces it ends with.
    `limit` is where the selection starts when it starts in the same text. */
export function wordEnd(text: string, i: number, limit = 0): number {
  while (i > limit && SPACE.test(text[i - 1])) i--;
  if (i > 0 && WORD.test(text[i - 1])) while (i < text.length && WORD.test(text[i])) i++;
  return i;
}
