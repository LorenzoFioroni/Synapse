// Hooks the pages put on `window` for other tabs, other modules and the browser tests.
import type ForceGraph from 'force-graph';
import type { PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist';
import type { PageWrapper } from './viewer/viewer.js';

/** What the PDF viewer (viewer.ts) offers its add-ons. */
export interface ZcViewer {
  readonly pdfDoc: PDFDocumentProxy | null;
  readonly scale: number;
  container: HTMLElement;
  /** Pages that have a canvas now (for the browser checklists). */
  renderedPages?(): number[];
}

/** The part of Mermaid we call. */
export interface Mermaid {
  initialize(options: { startOnLoad: boolean; securityLevel: string; theme: string }): void;
  render(id: string, code: string): Promise<{ svg: string }>;
}

declare global {
  /** The viewer's page events (viewer.ts), heard by its add-ons. */
  interface DocumentEventMap {
    'zc:pagecreated': CustomEvent<{ wrapper: PageWrapper; page: PDFPageProxy }>;
    'zc:pagerendered': CustomEvent<{ wrapper: PageWrapper; page: PDFPageProxy; num: number }>;
  }
  /** A long press on a touch screen (ui/longpress.ts). */
  interface HTMLElementEventMap {
    'zc:longpress': CustomEvent<{ x: number; y: number }>;
  }

  interface Window {
    /** Mermaid (vendor/mermaid/mermaid.min.js), loaded when a note has a diagram (richRender.ts). */
    mermaid?: Mermaid;
    /** pdf.js (vendor/pdfjs/pdf.min.js, a classic script in viewer.html). */
    pdfjsLib?: typeof import('pdfjs-dist');
    zcViewer?: ZcViewer;
    /** Points this viewer tab at another spot (notesBridge.ts); other app tabs call it (api.ts). */
    zcViewerGoto?: (to: { page?: number | string | null; q?: string | null; rect?: string | null }) => void;
    zcViewerBridge?: unknown;   // for the browser tests
    /** links.ts: Back/Forward between spots in the PDF; jumpRecorded is used by notesBridge.ts. */
    zcLinks?: { jumpRecorded?: (run: () => unknown, href: string) => void; [k: string]: unknown };
    /** force-graph (vendor/force-graph/force-graph.min.js), loaded when a graph is shown (forceGraph.ts). */
    ForceGraph?: typeof ForceGraph;
    /** The open note (notes/notesView.ts), read-only, for the browser tests. */
    zcNotes?: { text(): string | null; path(): string | null; cursor(): number | null; focused(): boolean };
    /** The notes graphs (notesGraph.ts), for the browser tests. */
    zcGraph?: unknown;
    /** The change feed's poll and state (changes.ts), for the browser tests. */
    zcChanges?: { poll(): Promise<void>; state(): { seq: number | null; epoch: string | null; pending: string[] } };
    /** The open PDF's annotations (viewer/annotations.ts), read-only, for the browser tests. */
    zcAnnotations?: { ready: Promise<void>; list(): unknown[]; attachment(): number | null; refresh(): Promise<void> };
  }
}

