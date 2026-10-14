// Hooks the pages put on `window` for other tabs, other modules and the browser tests.
import type { Item } from './types.js';

declare global {
  interface Window {
    /** The change feed's poll and state (changes.ts), for the browser tests. */
    zcChanges?: { poll(): Promise<void>; state(): { seq: number | null; epoch: string | null; pending: string[] } };
  }
}

export type { Item };
