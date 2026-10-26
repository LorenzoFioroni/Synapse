// Metadata Check in the app: background checking with notifications, review dialog,
// and settings with ignored papers and rejected suggestions.
import type { PluginContext, PluginItem } from "../../api.js";
import { startJob, type Job } from "../../../notifications/center.js";
import { openReviewDialog, unchangedFields, type Article, type Change } from "../../../views/metadataReview.js";
import type { Item } from "../../../types.js";
import { plural } from "../../../util.js";

interface StatusResponse {
  last_checked: number | null;
  interval_days: number;
  ignored: number;
  ignored_papers: number;
  running: boolean;
  failed?: number;
  done: number;
  total: number;
  with_changes: number;
  unreviewed?: number;
  last_run?: { total: number; with_changes: number; at: number } | null;
}

interface IgnoredItem {
  id: string;
  title: string;
  what: string[];
  pending: boolean;
  in_library: boolean;
  at?: number | null;
}

interface IgnoredPaper {
  id: string;
  title: string;
  in_library: boolean;
  at?: number | null;
}

interface IgnoredResponse {
  ignored: IgnoredItem[];
  ignored_papers?: IgnoredPaper[];
}

export function activate(p: PluginContext): void {
  const fieldId = `${p.id}.metadata_check`;

  const verdictOf = (item: PluginItem): string => {
    const f = item._f as Record<string, unknown> | undefined;
    return String((f && f[fieldId]) || "");
  };

  const suggests = (item: PluginItem): boolean => /^(Review:|Published version:)/.test(verdictOf(item));
  const anySuggestion = (items: PluginItem[]): boolean =>
    items.length > 0 && !items.some((i) => i._outside) && items.some(suggests);

  const anyIgnored = (items: PluginItem[]): boolean =>
    items.length > 0 && !items.some((i) => i._outside) && items.some((i) => verdictOf(i).includes("· ignored") && !verdictOf(i).includes("ignored paper"));

  const anyIgnoredPaper = (items: PluginItem[]): boolean =>
    items.length > 0 && !items.some((i) => i._outside) && items.some((i) => verdictOf(i).includes("ignored paper"));

  let activeJob: Job | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let unreviewedCount = 0;
  let isChecking = false;
  let disposed = false;
  let lastCompleted = 0;

  async function updateStatus(): Promise<StatusResponse> {
    const st = await p.backend.get<StatusResponse>("/status");
    unreviewedCount = st.unreviewed ?? st.with_changes ?? 0;
    return st;
  }

  async function pollStatus(): Promise<void> {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    if (isChecking || disposed) return;
    isChecking = true;
    try {
      const st = await updateStatus();
      if (disposed) return;
      if (!st.running && (st.last_run?.at || 0) > lastCompleted) {
        lastCompleted = st.last_run!.at;
        if (!activeJob) activeJob = startJob("Checking metadata…");
      }
      if (st.running) {
        if (!activeJob) {
          activeJob = startJob("Checking metadata…");
        }
        activeJob.set("Checking metadata…", st.done, st.total);
        pollTimer = setTimeout(pollStatus, 400);
      } else if (activeJob) {
        const changes = st.with_changes ?? 0;
        const total = st.total || st.done || 0;
        if (st.failed) {
          activeJob.finish(`Could not verify ${plural(st.failed, "paper")}; ${plural(changes, "paper")} have changes to review`, {
            type: "warning", desktop: false, source: p.id,
            action: changes ? { label: "Review", run: openReview } : undefined,
          });
        } else if (changes > 0) {
          activeJob.finish(`${plural(changes, "paper")} have metadata changes`, {
            desktop: true, source: p.id,
            type: "success",
            action: {
              label: "Review",
              run: () => { openReview(); },
            },
          });
        } else {
          activeJob.finish(`All ${total} papers up to date`, {
            desktop: false, source: p.id,
            type: "success",
          });
        }
        activeJob = null;
      }
    } catch {
      if (!disposed && activeJob) pollTimer = setTimeout(pollStatus, 2000);
    } finally {
      isChecking = false;
    }
  }

  async function runNow(): Promise<void> {
    if (!activeJob) {
      activeJob = startJob("Checking metadata…");
    }
    try {
      await p.backend.post("/run");
      await pollStatus();
    } catch (error) {
      activeJob?.finish(String(error), { type: "error", source: p.id });
      activeJob = null;
    }
  }

  async function openReview(): Promise<void> {
    const res = await p.backend.get<{
      articles: Array<{
        id: string;
        current: Item;
        fetched: Partial<Item>;
        changes: Change[];
        published?: string | null;
      }>;
    }>("/review");

    if (!res.articles || !res.articles.length) {
      p.notify("No metadata changes to review", { type: "info", transient: true });
      return;
    }

    const articles: Article[] = res.articles.map((a, i) => {
      const item = a.current;
      const changes = a.changes || [];
      return {
        id: a.id,
        n: i + 1,
        item,
        status: "changes",
        changes,
        unchanged: unchangedFields(item, changes),
        error: null,
        published: a.published || null,
      };
    });

    await openReviewDialog(articles, {
      title: "Review Metadata Changes",
      onRejectSuggestion: async (a, c) => {
        await p.backend.post("/ignore", { ids: [a.id], field: c.key, value: String(res.articles.find((item) => item.id === a.id)?.fetched[c.key] ?? c.fetched) });
        await updateStatus();
      },
      onRejectArticle: async (a) => {
        await p.backend.post("/ignore", { ids: [a.id] });
        await updateStatus();
      },
      onIgnorePaper: async (a) => {
        await p.backend.post("/ignore-paper", { ids: [a.id] });
        await updateStatus();
      },
      onApplied: async (savedIds) => {
        await p.backend.post("/applied", { ids: savedIds });
        await updateStatus();
      },
    });
    await updateStatus();
  }

  async function unignore(ids: string[]): Promise<void> {
    await p.backend.post("/unignore", { ids });
    await updateStatus();
  }

  async function unignorePaper(ids: string[]): Promise<void> {
    await p.backend.post("/unignore-paper", { ids });
    await updateStatus();
  }

  async function ignore(items: PluginItem[]): Promise<void> {
    const ids = items.filter(suggests).map((i) => i.id);
    if (!ids.length) return;
    const r = await p.backend.post<{ ignored: string[]; pending: string[] }>("/ignore", { ids });
    const n = r.ignored.length + r.pending.length;
    p.notify(n === 1 ? "Ignoring this suggestion" : `Ignoring suggestions for ${n} items`, {
      transient: true,
      action: { label: "Undo", run: () => { unignore(ids); } },
    });
    await updateStatus();
  }

  async function ignorePaper(ids: string[]): Promise<void> {
    if (!ids.length) return;
    await p.backend.post("/ignore-paper", { ids });
    p.notify(ids.length === 1 ? "Ignoring this paper in metadata checks" : `Ignoring ${ids.length} papers in metadata checks`, {
      transient: true,
      action: { label: "Undo", run: () => { unignorePaper(ids); } },
    });
    await updateStatus();
  }

  p.commands.register({ id: "run", title: "Run Now", run: runNow });
  p.commands.register({
    id: "review",
    title: "Review Changes",
    when: () => unreviewedCount > 0,
    run: openReview,
  });

  p.itemActions.register({
    id: "ignore",
    title: "Ignore Metadata Suggestion",
    icon: "close",
    when: anySuggestion,
    run: (items) => ignore(items),
  });

  p.itemActions.register({
    id: "unignore",
    title: "Stop Ignoring Metadata Suggestion",
    when: anyIgnored,
    run: (items) => unignore(items.map((i) => i.id)),
  });

  p.itemActions.register({
    id: "ignore-paper",
    title: "Ignore Paper in Metadata Check",
    when: (items) => items.length > 0 && !items.some((i) => i._outside) && !anyIgnoredPaper(items),
    run: (items) => ignorePaper(items.map((i) => i.id)),
  });

  p.itemActions.register({
    id: "unignore-paper",
    title: "Stop Ignoring Paper in Metadata Check",
    when: anyIgnoredPaper,
    run: (items) => unignorePaper(items.map((i) => i.id)),
  });

  // Settings page
  p.settingsPage.register({
    id: "status",
    title: p.name,
    mount(el) {
      let gone = false;
      const esc = p.ui.escapeHtml;

      async function draw() {
        const [st, ig] = await Promise.all([
          updateStatus(),
          p.backend.get<IgnoredResponse>("/ignored"),
        ]);
        if (gone) return;

        const last = st.last_checked
          ? p.ui.formatDate(new Date(st.last_checked * 1000).toISOString(), true)
          : "not yet";

        const pending = unreviewedCount;

        const ignoredSuggestions = (ig.ignored || []).map((e) => `
          <li class="mc-ignored" data-id="${esc(e.id)}">
            <div class="mc-what">
              <strong>${esc(e.title)}</strong>${e.in_library ? '' : ' <span class="mc-dim">(no longer in the library)</span>'}
              <div class="mc-dim">${e.pending ? 'Whatever the next check suggests' : esc(e.what.join(' · ') || 'Suggestions at the time')}</div>
            </div>
            <button class="btn btn-sm" data-mc-unignore>Stop Ignoring</button>
          </li>`).join('');

        const ignoredPapers = (ig.ignored_papers || []).map((e) => `
          <li class="mc-ignored-paper" data-id="${esc(e.id)}">
            <div class="mc-what">
              <strong>${esc(e.title)}</strong>${e.in_library ? '' : ' <span class="mc-dim">(no longer in the library)</span>'}
              <div class="mc-dim">Ignored from metadata checks</div>
            </div>
            <button class="btn btn-sm" data-mc-unignore-paper>Stop Ignoring</button>
          </li>`).join('');

        el.innerHTML = `
          <div class="mc-run">
            <button class="btn" data-mc-run>${p.ui.icon('refresh')} Run Now</button>
            <button class="btn" data-mc-review ${pending === 0 ? 'disabled' : ''}>
              ${p.ui.icon('check')} Review Changes
              ${pending > 0 ? `<span class="badge accent">${pending}</span>` : ''}
            </button>
            <span class="mc-dim">Last check: ${esc(last)}</span>
          </div>

          <h4 class="mc-head">Ignored papers</h4>
          ${ignoredPapers ? `<ul class="mc-list mc-ignored-papers">${ignoredPapers}</ul>` : '<p class="mc-dim">None. Ignore a paper from the review dialog.</p>'}

          <h4 class="mc-head">Rejected suggestions</h4>
          ${ignoredSuggestions ? `<ul class="mc-list mc-ignored-suggestions">${ignoredSuggestions}</ul>` : '<p class="mc-dim">None. Ignore one from an item’s menu (right-click) or the command palette.</p>'}`;
      }

      async function onClick(ev: MouseEvent) {
        const t = ev.target as HTMLElement | null;
        if (!t) return;
        if (t.closest("[data-mc-run]")) {
          await runNow();
          return;
        }
        if (t.closest("[data-mc-review]")) {
          await openReview();
          draw();
          return;
        }
        const rowUnignore = t.closest("[data-mc-unignore]") && t.closest<HTMLElement>("[data-id]");
        if (rowUnignore) {
          await unignore([String(rowUnignore.dataset.id)]);
          await draw();
          return;
        }
        const rowUnignorePaper = t.closest("[data-mc-unignore-paper]") && t.closest<HTMLElement>("[data-id]");
        if (rowUnignorePaper) {
          await unignorePaper([String(rowUnignorePaper.dataset.id)]);
          await draw();
          return;
        }
      }

      el.addEventListener("click", onClick);
      const offChange = p.onChange((kinds) => {
        if (kinds.includes("fields")) draw();
      });
      draw();

      return () => {
        gone = true;
        offChange();
        el.removeEventListener("click", onClick);
      };
    },
  });

  // Listen to change feed to update status or detect background runs
  const offAll = p.onChange((kinds) => {
    if (kinds.includes("fields")) {
      pollStatus();
    }
  });

  // Check status on activate
  updateStatus().then((st) => {
    lastCompleted = st.last_run?.at || 0;
    if (st.running) pollStatus();
  }).catch(() => {});

  p.onUnload(() => {
    disposed = true;
    activeJob?.close();
    activeJob = null;
    if (pollTimer) clearTimeout(pollTimer);
    offAll();
  });
}
