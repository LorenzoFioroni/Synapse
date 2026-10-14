// Hooks the pages put on `window` for other tabs, other modules and the browser tests.
import type ForceGraph from 'force-graph';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import type { Item } from './types.js';

/** The part of Mermaid we call. */
/** What the PDF viewer (viewer.ts) offers its add-ons. */
export interface ZcViewer {
  readonly pdfDoc: PDFDocumentProxy | null;
  readonly scale: number;
  container: HTMLElement;
  /** Pages that have a canvas now (for the browser checklists). */
  renderedPages?(): number[];
}

export interface Mermaid {
  initialize(options: { startOnLoad: boolean; securityLevel: string; theme: string }): void;
  render(id: string, code: string): Promise<{ svg: string }>;
}

declare global {
  interface Window {
    /** The change feed's poll and state (changes.ts), for the browser tests. */
    /** Mermaid (vendor/mermaid/mermaid.min.js), loaded when a note has a diagram (richRender.ts). */
    mermaid?: Mermaid;
    /** pdf.js (vendor/pdfjs/pdf.min.js, a classic script in viewer.html). */
    pdfjsLib?: typeof import('pdfjs-dist');
    zcViewer?: ZcViewer;
    /** force-graph (vendor/force-graph/force-graph.min.js), loaded when a graph is shown (forceGraph.ts). */
    ForceGraph?: typeof ForceGraph;
    /** The notes graphs (notesGraph.ts), for the browser tests. */
    zcGraph?: unknown;
    zcChanges?: { poll(): Promise<void>; state(): { seq: number | null; epoch: string | null; pending: string[] } };
  }
}

export type { Item };
