// The plugin API for a plugin's frontend: what `activate(p)` receives. This file holds types only,
// with type-only references to the public SDK modules (docs/plugins.md):
// write `/** @param {import('./api').PluginContext} p */` in a JavaScript plugin, or import the
// types in a TypeScript one. Only what is declared here is promised to plugins; the rest of the
// app's code may change between versions.

/** Turns a registration off again (everything is also turned off when the plugin stops). */
export type Dispose = () => void;
/** A mount function may return a function that cleans up when its element goes away. */
export type Cleanup = void | (() => void);

/** A library item as the app's JSON API sends it. */
export interface PluginItem {
  id: string;
  title?: string;
  authors?: string;
  year?: number | string | null;
  citation_key?: string | null;
  doi?: string | null;
  url?: string | null;
  item_type?: string;
  tags?: string[];
  folder_ids?: number[];
  /** A paper from a plugin's list that is not in the library (its id is "x:<key>"): item-pane
      sections are shown for it too. */
  _outside?: boolean;
  [field: string]: unknown;
}

export interface CommandSpec {
  /** Unique within the plugin. */
  id: string;
  /** Shown in the command palette (⌘⇧P / Ctrl+Shift+P) after the plugin's name. */
  title: string;
  /** e.g. 'Mod-Alt-r' (Mod: ⌘ on a Mac, Ctrl elsewhere). Needs Mod or Alt and must not be one of
      Synapse's own keys; a key that cannot be used is left out (the command still works). */
  key?: string;
  /** Offered only while this returns true. */
  when?: () => boolean;
  run: () => unknown;
}

export interface PageSpec {
  id: string;
  /** The sidebar entry's name and the page's heading. */
  title: string;
  /** One of the app's icon names (e.g. 'calendar', 'list', 'globe'); a puzzle piece by default. */
  icon?: string;
  /** Draw the page into `el` (inside an element with data-plugin="<plugin id>"). */
  mount: (el: HTMLElement) => Cleanup;
}

export interface ItemSectionSpec {
  id: string;
  title: string;
  /** Shown only for the items this returns true for. */
  when?: (item: PluginItem) => boolean;
  /** Draw the section for `item` into `el`; called again when another item is shown. */
  mount: (el: HTMLElement, item: PluginItem) => Cleanup;
}

export interface ItemActionSpec {
  id: string;
  /** In the item menu (right-click) and in the command palette, for the selected items. */
  title: string;
  icon?: string;
  /** Also offered for papers from a list that are not in the library (`item._outside`); without
      it, the action is left out whenever such a paper is selected. */
  outside?: boolean;
  when?: (items: PluginItem[]) => boolean;
  run: (items: PluginItem[]) => unknown;
}

export interface SettingsSectionSpec {
  id: string;
  title: string;
  icon?: string;
  /** Kept for plugins written for an earlier Synapse: a plugin's part now always goes in the
      plugin's own section in Settings. */
  after?: string;
  /** Keys of the manifest's settings this part draws itself; the plugin's section does not list
      them again. */
  shows?: string[];
  /** Draw the section into `el` each time Settings opens; return a function to clean up. */
  mount: (el: HTMLElement) => Cleanup;
}

export interface NotifyOptions {
  type?: 'info' | 'success' | 'warning' | 'error';
  /** Persistent navigation after a reload, for one of this plugin’s paper sources. */
  sourceId?: string;
  /** Offer OS delivery when enabled by the user, independently of severity. */
  desktop?: boolean;
  /** Keep a brief confirmation/Undo at the bottom of the window instead of in history. */
  transient?: boolean;
  action?: { label: string; run: () => void };
  /** Milliseconds for a transient toast; ignored by the notification center. */
  timeout?: number;
}

/** What changed, as the change feed names it: something in this or another window, on another
    device, by Claude or by background work. */
export type ChangeKind = 'library' | 'notes' | 'tasks' | 'annotations' | 'plugins' | 'fields' | 'sources';

/** Plain text around the cursor; no editor-library objects cross the plugin boundary.
 * The newest registration is active; disposing it restores the preceding provider. */
export interface SuggestionSource {
  ready(): boolean;
  complete(prefix: string, suffix: string, signal: AbortSignal): Promise<string>;
  /** Pause after typing in milliseconds (350 by default). */
  delay?: number;
}

/** A proposed metadata difference. Values are saved only after the user presses Apply. */
export interface MetadataChange {
  key: string; current: unknown; fetched: unknown; isNew: boolean; checked: boolean;
}
export interface MetadataReviewArticle {
  id: string; current: PluginItem; changes: MetadataChange[]; published?: string | null;
}
export interface MetadataReviewOptions {
  title?: string;
  onRejectSuggestion?: (article: { id: string }, change: MetadataChange) => Promise<void> | void;
  onRejectArticle?: (article: { id: string }) => Promise<void> | void;
  onIgnorePaper?: (article: { id: string }) => Promise<void> | void;
  onApplied?: (ids: string[]) => Promise<void> | void;
}
export interface PluginJob {
  set(message: string, done?: number, total?: number): void;
  finish(message?: string, options?: NotifyOptions): void;
  close(): void;
}

/** Host-owned Notes layout and navigation; no editor or app-store objects are exposed. */
export interface NotesVisualizationContext {
  main: HTMLElement;
  currentPath(): string | null;
  openNote(path: string): void;
  open(focus: string | null): void;
  setOpen(open: boolean): void;
  closed(): void;
}
export interface NotesVisualizationSpec {
  id: string; title: string; icon?: string;
  attach(context: NotesVisualizationContext): void;
  section(): HTMLElement;
  showNote(path: string | null): void;
  changed(): void;
  open(focus: string | null): Promise<void>;
  close(options?: { notify?: boolean }): void;
  isOpen(): boolean;
  destroy(): void;
}

export interface PluginContext {
  tasks: { register(integration: import('./task-runtime.js').TaskIntegration): Dispose };
  markdown: import('./markdown.js').MarkdownApi;
  /** The plugin's id and what its manifest says. */
  readonly id: string;
  readonly name: string;
  readonly version: string;
  /** The user's values of the settings the manifest declares (as they were when the plugin started;
      a change of settings restarts nothing, so read them when needed via `settings()`). */
  settings(): Promise<Record<string, unknown>>;
  /** Change some of those values (checked against the manifest); resolves to all of them. The
      plugin's backend hears the change (`@plugin.settings.on_change`). On a device other than the
      computer running Synapse this first asks for the user's passkey; it rejects when they cancel. */
  saveSettings(changes: Record<string, unknown>): Promise<Record<string, unknown>>;

  /** URLs of host-vendored offline dependencies; currently force-graph. */
  assets: { vendor(name: 'force-graph'): string };
  notes: {
    enabled(): boolean;
    current(): string | null;
    open(path: string): Promise<void>;
    /** One visualization at a time, alongside and in place of the note editor. */
    visualizations: {
      register(spec: NotesVisualizationSpec): Dispose;
      open(id: string, focus?: string | null): Promise<void>;
      toggle(id: string): Promise<void>;
    };
  };
  editor: {
    suggestions: { register(source: SuggestionSource): Dispose };
    /** Called when a note editor opens; activation should also check existing editors. */
    onOpened(fn: () => void): Dispose;
  };
  commands: { register(spec: CommandSpec): Dispose };
  pages: {
    register(spec: PageSpec): Dispose;
    /** A count or short text on the page's sidebar entry; null removes it. */
    setBadge(pageId: string, value: string | number | null): void;
    open(pageId: string): void;
  };
  itemPane: { register(spec: ItemSectionSpec): Dispose };
  itemActions: { register(spec: ItemActionSpec): Dispose };
  /** A section of its own in the Settings page, for what the manifest's settings cannot show
      (a status, a test button, a list to pick from). */
  settingsPage: { register(spec: SettingsSectionSpec): Dispose; open(): void };
  /** The plugin's lists of papers (`@plugin.item_source` in its backend). */
  sources: {
    /** Show the list `sourceId` (leaving Notes, Tasks or a page). */
    open(sourceId: string): void;
    /** Ask for the lists again now (their badges, and the one shown), after the plugin changed
        them from this window: other windows hear it through the change feed. */
    refresh(): Promise<void>;
  };

  /** A persistent notification in the bell center; OS delivery is independent of severity and requires user opt-in. */
  notify(message: string, options?: NotifyOptions): void;
  /** Progress in the notification center; closed automatically when the plugin stops. */
  jobs: { start(message: string): PluginJob };

  /** The plugin's own routes (`plugin.route` in its backend), under /api/plugins/<id>/. Each resolves
      to the parsed JSON answer and throws an Error with the server's message on failure. */
  backend: {
    get<T = unknown>(path: string, query?: Record<string, string | number | boolean | undefined>): Promise<T>;
    post<T = unknown>(path: string, body?: unknown, options?: { signal?: AbortSignal }): Promise<T>;
    patch<T = unknown>(path: string, body?: unknown): Promise<T>;
    put<T = unknown>(path: string, body?: unknown): Promise<T>;
    delete<T = unknown>(path: string): Promise<T>;
  };

  library: {
    /** The items selected in the list. */
    selected(): PluginItem[];
    refresh(): Promise<void>;
    /** Open the first attachment or the item's URL, as in the item pane. */
    openPdf(id: string): Promise<void>;
    /** Edit metadata (requires library: ["edit"]); uses the app's validation and change feed. */
    update(id: string, changes: Record<string, unknown>): Promise<PluginItem>;
    /** One item, with its abstract and attachments. */
    item(id: string): Promise<PluginItem>;
    /** Show an item in the library (leaving Notes, Tasks or a page). */
    show(id: string): void;
  };

  /** Hear about changes (`kinds`: what changed). Changes made in this window count as 'library'
      when the item list reloads. */
  onChange(fn: (kinds: ChangeKind[]) => void): Dispose;
  /** Run when the plugin stops (turned off, restarted, or the page reloads). */
  onUnload(fn: () => void): void;

  ui: {
    /** The app's icon as SVG markup (the names the sidebar uses: 'calendar', 'list', 'star' …). */
    icon(name: string, className?: string): string;
    /** Explicit Apply, per-field rejection and paper exclusion; requires library: ["edit"].
        The dialog closes when the plugin stops. Inputs are copied. */
    reviewMetadata(articles: MetadataReviewArticle[], options?: MetadataReviewOptions): Promise<unknown>;
    /** Text made safe to put into HTML. */
    escapeHtml(text: unknown): string;
    confirm(message: string, options?: { title?: string; confirm?: string; danger?: boolean; detail?: string }): Promise<boolean>;
    prompt(label: string, options?: { title?: string; value?: string; placeholder?: string; confirm?: string; hint?: string }): Promise<string | null>;
    /** Format an ISO date the way the app does. */
    formatDate(iso: string, withTime?: boolean): string;
  };
}

/** What a plugin's frontend module exports. */
export interface PluginModule {
  activate(p: PluginContext): void | Promise<void>;
  deactivate?(p: PluginContext): void;
}
