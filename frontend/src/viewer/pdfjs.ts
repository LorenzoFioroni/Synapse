/**
 * Loads pdf.js (an ES module) and hands it to the viewer's modules as `window.pdfjsLib`.
 * viewer.html loads this module before the others; module scripts run in document order.
 */
import * as pdfjsLib from '../../vendor/pdfjs/pdf.min.mjs';

// pdf.js reads a page's text with `for await (… of ReadableStream)`. Safari on iPadOS/iOS does not
// make ReadableStream async-iterable, so getTextContent (text layer, search, links) throws
// "undefined is not a function". Polyfill only that, with the spec's cancel-on-early-exit.
const streamProto = ReadableStream.prototype as unknown as Record<symbol, unknown>;
if (!(Symbol.asyncIterator in streamProto)) {
  streamProto[Symbol.asyncIterator] = async function* (this: ReadableStream) {
    const reader = this.getReader();
    let finished = false;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) { finished = true; return; }
        yield value;
      }
    } finally {
      if (!finished) await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  };
}

window.pdfjsLib = pdfjsLib;
