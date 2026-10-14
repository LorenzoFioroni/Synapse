// Types for the parts of the vendored libraries (frontend/vendor) that our code uses. The
// libraries ship as plain JavaScript bundles; these interfaces describe only what we call, so
// they stay small. Where a library hands us its own internal objects (markdown-it's parser
// state), the fields we do not touch are left open.

/* ------------------------------------------------------------------ markdown-it (vendor/md) */

export interface MdToken {
  type: string;
  tag: string;
  content: string;
  markup: string;
  info: string;
  meta: any;               // set by our own rules (render.ts); its shape depends on the token type
  map: [number, number] | null;
  block: boolean;
  hidden: boolean;
  nesting: number;
  children: MdToken[] | null;
  attrGet(name: string): string | null;
  attrSet(name: string, value: string): void;
  attrJoin(name: string, value: string): void;
}

export interface MdInlineState {
  src: string;
  pos: number;
  posMax: number;
  push(type: string, tag: string, nesting: number): MdToken;
}

export interface MdBlockState {
  src: string;
  bMarks: number[];
  eMarks: number[];
  tShift: number[];
  line: number;
  push(type: string, tag: string, nesting: number): MdToken;
}

export interface MdCoreState {
  tokens: MdToken[];
  env: Record<string, any>;
  Token: new (type: string, tag: string, nesting: number) => MdToken;
}

export type MdRenderRule = (tokens: MdToken[], idx: number, options: unknown, env: Record<string, any>, self: MdRenderer) => string;

export interface MdRenderer {
  rules: Record<string, MdRenderRule | undefined>;
  renderToken(tokens: MdToken[], idx: number, options: unknown): string;
  renderInlineAsText(tokens: MdToken[], options: unknown, env: Record<string, any>): string;
}

interface MdRuler<R> {
  before(name: string, rule: string, fn: R, options?: { alt?: string[] }): void;
  after(name: string, rule: string, fn: R): void;
  push(rule: string, fn: R): void;
}

export interface MarkdownIt {
  block: { ruler: MdRuler<(state: MdBlockState, startLine: number, endLine: number, silent: boolean) => boolean> };
  inline: { ruler: MdRuler<(state: MdInlineState, silent: boolean) => boolean> };
  core: { ruler: MdRuler<(state: MdCoreState) => void> };
  renderer: MdRenderer;
  disable(rule: string): this;
  use(plugin: unknown): this;
  render(src: string, env?: Record<string, any>): string;
  renderInline(src: string, env?: Record<string, any>): string;
  parse(src: string, env: Record<string, any>): MdToken[];
}

export interface KatexOptions {
  displayMode?: boolean;
  throwOnError?: boolean;
  strict?: string;
  trust?: boolean;
  output?: string;
  macros?: Record<string, unknown>;
  maxExpand?: number;
}

/** What vendor/md/md.js exports. */
export interface MdLib {
  MarkdownIt: new (options: { html: boolean; linkify: boolean; typographer: boolean }) => MarkdownIt;
  footnote: unknown;
  katex: { renderToString(tex: string, options: KatexOptions): string };
}
