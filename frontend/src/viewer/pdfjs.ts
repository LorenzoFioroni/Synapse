/**
 * Loads pdf.js (an ES module) and hands it to the viewer's modules as `window.pdfjsLib`.
 * viewer.html loads this module before the others; module scripts run in document order.
 */
import * as pdfjsLib from '../../vendor/pdfjs/pdf.min.mjs';

window.pdfjsLib = pdfjsLib;
