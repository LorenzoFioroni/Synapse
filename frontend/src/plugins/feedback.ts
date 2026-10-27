// Plugin-owned progress and metadata review. The host retires this scope on unload so
// dialogs and notification callbacks cannot outlive the context that created them.
import type { Job } from '../notifications/center.js';
import { startJob } from '../notifications/center.js';
import type { Item } from '../types.js';
import { openReviewDialog, unchangedFields } from '../views/metadataReview.js';
import type { PluginContext } from './api.js';

export function feedback(
  pid: string,
  stopped: () => boolean,
  guard: (fn: () => void) => () => void,
  update: (id: string, changes: Record<string, unknown>) => Promise<Item>,
): { jobs: PluginContext['jobs']; reviewMetadata: PluginContext['ui']['reviewMetadata']; dispose(): void } {
  const jobs = new Set<Job>();
  const dialogs = new Set<AbortController>();
  const live = () => { if (stopped()) throw new DOMException('Plugin stopped', 'AbortError'); };
  return {
    jobs: {
      start(message) {
        live();
        const job = startJob(message);
        jobs.add(job);
        let done = false;
        return {
          set(text, n, total) { if (!stopped() && !done) job.set(text, n, total); },
          finish(text, options = {}) {
            if (stopped() || done) return;
            done = true; jobs.delete(job);
            job.finish(text, { type: options.type, source: pid, desktop: options.desktop,
              target: options.sourceId ? `source:${pid}.${options.sourceId}` : undefined,
              action: options.action ? { label: options.action.label, run: guard(() => { live(); options.action!.run(); }) } : null });
          },
          close() { if (!done) { done = true; job.close(); } jobs.delete(job); },
        };
      },
    },
    async reviewMetadata(input, options = {}) {
      live();
      const controller = new AbortController();
      dialogs.add(controller);
      try {
        const articles = structuredClone(input).map((a, n) => ({
          id: a.id, n: n + 1, item: a.current as Item, status: 'changes' as const,
          changes: a.changes, unchanged: unchangedFields(a.current as Item, a.changes),
          error: null, published: a.published,
        }));
        return await openReviewDialog(articles, {
          ...options, signal: controller.signal, update,
          onRejectSuggestion: options.onRejectSuggestion ? (a, c) => { live(); return options.onRejectSuggestion!({ id: a.id }, structuredClone(c)); } : undefined,
          onRejectArticle: options.onRejectArticle ? a => { live(); return options.onRejectArticle!({ id: a.id }); } : undefined,
          onIgnorePaper: options.onIgnorePaper ? a => { live(); return options.onIgnorePaper!({ id: a.id }); } : undefined,
          onApplied: options.onApplied ? ids => { live(); return options.onApplied!(ids); } : undefined,
        });
      } finally { dialogs.delete(controller); }
    },
    dispose() {
      for (const controller of dialogs) controller.abort();
      dialogs.clear();
      for (const job of jobs) job.close();
      jobs.clear();
    },
  };
}
