// Metadata Check in the app: background checking with notifications (a desktop one for published
// and new arXiv versions), the review dialog with the PDF replacement, and settings with ignored
// papers and rejected suggestions.
import type { PluginContext, PluginItem, PluginJob, MetadataChange } from "../../../frontend/src/plugins/api.js";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

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

interface NewVersion { version: number; have: number | null; how: string; url: string; detail: string; highlights: number }

interface ReviewArticle {
  id: string;
  current: PluginItem;
  fetched: Partial<PluginItem>;
  changes: MetadataChange[];
  published?: string | null;
  version?: NewVersion | null;
}

interface News { id: string; title: string; published?: string | null; version?: number | null }

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

  const suggests = (item: PluginItem): boolean => /^(Review:|Published version:|New arXiv version:)/.test(verdictOf(item));
  const offersVersion = (items: PluginItem[]): boolean =>
    items.length === 1 && !items[0]._outside && verdictOf(items[0]).startsWith("New arXiv version:");
  const anySuggestion = (items: PluginItem[]): boolean =>
    items.length > 0 && !items.some((i) => i._outside) && items.some(suggests);

  const anyIgnored = (items: PluginItem[]): boolean =>
    items.length > 0 && !items.some((i) => i._outside) && items.some((i) => verdictOf(i).includes("· ignored") && !verdictOf(i).includes("ignored paper"));

  const anyIgnoredPaper = (items: PluginItem[]): boolean =>
    items.length > 0 && !items.some((i) => i._outside) && items.some((i) => verdictOf(i).includes("ignored paper"));

  let activeJob: PluginJob | null = null;
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
        if (!activeJob) activeJob = p.jobs.start("Checking metadata…");
      }
      if (st.running) {
        if (!activeJob) {
          activeJob = p.jobs.start("Checking metadata…");
        }
        activeJob.set("Checking metadata…", st.done, st.total);
        pollTimer = setTimeout(pollStatus, 400);
      } else if (activeJob) {
        const changes = st.with_changes ?? 0;
        const total = st.total || st.done || 0;
        const fresh = await claimNews();
        if (disposed) return;
        if (fresh.length) {
          activeJob.finish(newsMessage(fresh), {
            desktop: true, type: "success",
            action: { label: "Review", run: () => { openReview(); } },
          });
        } else if (st.failed) {
          activeJob.finish(`Could not verify ${plural(st.failed, "paper")}; ${plural(changes, "paper")} have changes to review`, {
            type: "warning", desktop: false, 
            action: changes ? { label: "Review", run: openReview } : undefined,
          });
        } else if (changes > 0) {
          activeJob.finish(`${plural(changes, "paper")} have metadata changes`, {
            desktop: true, 
            type: "success",
            action: {
              label: "Review",
              run: () => { openReview(); },
            },
          });
        } else {
          activeJob.finish(`All ${total} papers up to date`, {
            desktop: false, 
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

  async function claimNews(): Promise<News[]> {
    try {
      return (await p.backend.post<{ news: News[] }>("/news")).news || [];
    } catch {
      return [];
    }
  }

  function newsMessage(news: News[]): string {
    const published = news.filter((n) => n.published).length;
    const versions = news.filter((n) => n.version && !n.published).length;
    if (news.length === 1) {
      const n = news[0];
      const title = n.title.length > 80 ? `${n.title.slice(0, 79)}…` : n.title;
      return n.published ? `Published version of “${title}”` : `arXiv v${n.version} of “${title}”`;
    }
    return [published && `${plural(published, "paper")} published`, versions && `${plural(versions, "new arXiv version")}`]
      .filter(Boolean).join(" · ");
  }

  /** Found while no job of this window was watching (a check before the app was opened, say). */
  async function announce(): Promise<void> {
    const fresh = await claimNews();
    if (!fresh.length || disposed) return;
    p.notify(newsMessage(fresh), {
      type: "success", desktop: true,
      action: { label: "Review", run: () => { openReview(); } },
    });
  }

  async function replacePdf(id: string, version: NewVersion, title: string): Promise<boolean> {
    const n = version.highlights;
    const ok = await p.ui.confirm(`Replace the PDF of “${title}” with arXiv v${version.version}?`, {
      title: "Replace PDF",
      confirm: "Replace PDF",
      danger: n > 0,
      detail: n > 0
        ? `Its ${plural(n, "highlight")} and comments will be deleted: they mark places in the old version.`
        : "The current file is removed; the item and citations of it open the new version.",
    });
    if (!ok || disposed) return false;
    const job = p.jobs.start(`Downloading arXiv v${version.version}…`);
    try {
      const r = await p.backend.post<{ version: number; removed_annotations: number }>("/replace", { ids: [id] });
      job.finish(`Replaced with arXiv v${r.version}${r.removed_annotations ? `; ${plural(r.removed_annotations, "highlight")} deleted` : ""}`, { type: "success" });
      await p.library.refresh();
      await updateStatus();
      return true;
    } catch (error) {
      job.finish(String((error as Error)?.message || error), { type: "error" });
      return false;
    }
  }

  async function runNow(): Promise<void> {
    if (!activeJob) {
      activeJob = p.jobs.start("Checking metadata…");
    }
    try {
      await p.backend.post("/run");
      await pollStatus();
    } catch (error) {
      activeJob?.finish(String(error), { type: "error" });
      activeJob = null;
    }
  }

  function report(error: unknown): void {
    if (!disposed && (error as Error)?.name !== "AbortError") {
      p.notify(String(error), { type: "error", transient: true });
    }
  }

  function openReview(): Promise<void> {
    return reviewChanges().catch(report);
  }

  async function reviewChanges(): Promise<void> {
    const res = await p.backend.get<{ articles: ReviewArticle[] }>("/review");

    if (!res.articles || !res.articles.length) {
      p.notify("No metadata changes to review", { type: "info", transient: true });
      return;
    }

    if (disposed) return;
    const articles = res.articles.map((a) => ({
      ...a,
      replacement: a.version ? { label: `arXiv v${a.version.version} available`, detail: a.version.detail } : null,
    }));
    await p.ui.reviewMetadata(articles, {
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
      onReplace: async (a) => {
        const found = res.articles.find((item) => item.id === a.id);
        return found?.version ? replacePdf(a.id, found.version, String(found.current.title || "this paper")) : false;
      },
      onKeepCurrent: async (a) => {
        await p.backend.post("/keep-current", { ids: [a.id] });
        await updateStatus();
      },
    });
    if (!disposed) await updateStatus();
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
    id: "replace-pdf",
    title: "Replace PDF with New arXiv Version",
    icon: "refresh",
    when: offersVersion,
    run: async (items) => {
      const res = await p.backend.get<{ articles: ReviewArticle[] }>("/review");
      const found = res.articles.find((a) => a.id === items[0].id);
      if (found?.version) await replacePdf(found.id, found.version, String(found.current.title || "this paper"));
    },
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
      let shown = '';
      const esc = p.ui.escapeHtml;

      async function draw() {
        if (gone || disposed) return;
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

        const html = `
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
        // Field values change often during a check; rebuilding unchanged markup between a press and its
        // release would move the click to the section itself, and Run Now would do nothing.
        if (html !== shown) {
          shown = html;
          el.innerHTML = html;
        }
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
          await draw();
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

      const click = (ev: MouseEvent) => { void onClick(ev).catch(report); };
      const redraw = () => { void draw().catch(report); };
      el.addEventListener("click", click);
      const offChange = p.onChange((kinds) => {
        if (kinds.includes("fields")) redraw();
      });
      redraw();

      return () => {
        gone = true;
        offChange();
        el.removeEventListener("click", click);
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
    else void announce();
  }).catch(() => {});

  p.onUnload(() => {
    disposed = true;
    activeJob?.close();
    activeJob = null;
    if (pollTimer) clearTimeout(pollTimer);
    offAll();
  });
}
