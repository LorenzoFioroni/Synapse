// Hooks the pages put on `window` for other tabs, other modules and the browser tests.
import type ForceGraph from 'force-graph';
import type { Item } from './types.js';

/** The part of Mermaid we call. */
export interface Mermaid {
  initialize(options: { startOnLoad: boolean; securityLevel: string; theme: string }): void;
  render(id: string, code: string): Promise<{ svg: string }>;
}

declare global {
  interface Window {
    /** The change feed's poll and state (changes.ts), for the browser tests. */
    /** Mermaid (vendor/mermaid/mermaid.min.js), loaded when a note has a diagram (richRender.ts). */
    mermaid?: Mermaid;
    /** force-graph (vendor/force-graph/force-graph.min.js), loaded when a graph is shown (forceGraph.ts). */
    ForceGraph?: typeof ForceGraph;
    zcChanges?: { poll(): Promise<void>; state(): { seq: number | null; epoch: string | null; pending: string[] } };
  }
}

export type { Item };
