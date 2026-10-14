// Math macros for notes: your own commands and those of the LaTeX physics package.
//
// KaTeX has no packages, and a \newcommand only lasts for the formula that contains it. So the
// definitions are collected here and handed to every formula as KaTeX ``macros``:
//
//  - commands of the physics package (\vb, \dv, \pdv, \qty, \abs, \expval, \mel, \comm, …),
//    switchable in Settings; KaTeX knows none of them, so nothing it draws today changes;
//  - your own definitions in Settings → General → Math macros, for every note;
//  - \newcommand / \renewcommand / \def / \DeclareMathOperator written in any formula of a note,
//    for all formulas of that note (wherever they are; the defining formula shows nothing else).
//
// \newcommand with a default for its first argument (\newcommand{\x}[2][1]{…}) works too, which
// KaTeX alone does not support. DOM-free (tested with Node).

/** A \newcommand-like definition: ``opt`` is the default of an optional first argument. */
export interface Definition { name: string; nargs: number; opt: string | null; body: string; provide: boolean }

/** The part of KaTeX's macro expander that function macros use. */
export interface MacroContext {
  consumeSpaces(): void;
  consumeArg(): { tokens: { text: string }[] };
  future(): { text: string };
  popToken(): { text: string };
}

/** A KaTeX macro: replacement TeX, or a function that reads its arguments itself. */
export type Macro = string | ((ctx: MacroContext) => string);
export type Macros = Record<string, Macro>;

/* ------------------------------------------------------------------ reading definitions */

/** A balanced {…} at i: [content, index after it], or null. */
function group(s: string, i: number): [string, number] | null {
  if (s[i] !== '{') return null;
  let depth = 0;
  for (let j = i; j < s.length; j++) {
    const c = s[j];
    if (c === '\\') { j++; continue; }
    if (c === '{') depth++;
    else if (c === '}' && --depth === 0) return [s.slice(i + 1, j), j + 1];
  }
  return null;
}

/** A balanced [ … ] at i (braces protect a ]): [content, index after it], or null. */
function bracket(s: string, i: number): [string, number] | null {
  if (s[i] !== '[') return null;
  let depth = 0;
  for (let j = i + 1; j < s.length; j++) {
    const c = s[j];
    if (c === '\\') { j++; continue; }
    if (c === '{') depth++;
    else if (c === '}') depth--;
    else if (c === ']' && depth === 0) return [s.slice(i + 1, j), j + 1];
  }
  return null;
}

const ws = (s: string, i: number) => { while (i < s.length && /\s/.test(s[i])) i++; return i; };

/** The command name at i: "\name" (letters) or "{\name}". [name, index after it] or null. */
function commandName(s: string, i: number): [string, number] | null {
  if (s[i] === '{') {
    const g = group(s, i);
    if (!g) return null;
    const n = g[0].trim();
    return /^\\(?:[A-Za-z@]+|[^A-Za-z@\s])$/.test(n) ? [n, g[1]] : null;
  }
  const m = /^\\(?:[A-Za-z@]+|[^A-Za-z@\s])/.exec(s.slice(i, i + 80));
  return m ? [m[0], i + m[0].length] : null;
}

const START = /\\(newcommand|renewcommand|providecommand|DeclareMathOperator|def|gdef)(?![A-Za-z@])/g;
export const HAS_DEFINITIONS = /\\(?:newcommand|renewcommand|providecommand|DeclareMathOperator|g?def)(?![A-Za-z@])/;

/** One definition starting at i (the backslash of the keyword). */
function readDefinition(s: string, i: number, kind: string): (Omit<Definition, 'opt' | 'provide'> & { opt?: string | null; provide?: boolean; end: number }) | null {
  let j = i + kind.length + 1;
  if (kind === 'def' || kind === 'gdef') {
    const n = commandName(s, ws(s, j));
    if (!n || n[0].startsWith('{')) return null;
    j = n[1];
    const params = /^(?:\s*#\d)*\s*/.exec(s.slice(j))![0];
    j += params.length;
    const body = group(s, j);
    if (!body) return null;
    return { name: n[0], nargs: (params.match(/#\d/g) || []).length, body: body[0], end: body[1] };
  }
  if (s[j] === '*') j++;
  if (kind === 'DeclareMathOperator') {
    const star = s[j - 1] === '*';
    const n = commandName(s, ws(s, j));
    if (!n) return null;
    const body = group(s, ws(s, n[1]));
    if (!body) return null;
    return { name: n[0], nargs: 0, body: `\\operatorname${star ? '*' : ''}{${body[0]}}`, end: body[1] };
  }
  const n = commandName(s, ws(s, j));
  if (!n) return null;
  j = ws(s, n[1]);
  let nargs = 0, opt: string | null = null;
  const count = bracket(s, j);
  if (count) {
    if (!/^\s*\d\s*$/.test(count[0])) return null;
    nargs = Number(count[0]);
    j = ws(s, count[1]);
    const d = bracket(s, j);
    if (d) { opt = d[0]; j = ws(s, d[1]); }
  }
  const body = group(s, j);
  if (!body) return null;
  return { name: n[0], nargs, opt, body: body[0], end: body[1], provide: kind === 'providecommand' };
}

/**
 * The macro definitions in a piece of TeX, and ``rest``: the TeX without them.
 */
export function parseDefinitions(tex: string): { defs: Definition[]; rest: string } {
  const defs: Definition[] = [];
  let rest = '', last = 0;
  START.lastIndex = 0;
  for (let m = START.exec(tex); m; m = START.exec(tex)) {
    const d = readDefinition(tex, m.index, m[1]);
    if (!d) continue;   // not understood: left in place, KaTeX shows what is wrong
    defs.push({ name: d.name, nargs: d.nargs, opt: d.opt ?? null, body: d.body, provide: !!d.provide });
    rest += tex.slice(last, m.index);
    last = d.end;
    START.lastIndex = d.end;
  }
  return { defs, rest: defs.length ? rest + tex.slice(last) : tex };
}

/* ------------------------------------------------------------------ turning them into KaTeX macros */

/** TeX text of KaTeX tokens (as consumeArg gives them: in reverse order). */
function tokText(tokens: { text: string }[]): string {
  let out = '', prev = '';
  for (let k = tokens.length - 1; k >= 0; k--) {
    const x = tokens[k].text;
    if (/^\\[A-Za-z@]+$/.test(prev) && /^[A-Za-z@]/.test(x)) out += ' ';
    out += x;
    prev = x;
  }
  return out;
}

const arg = (ctx: MacroContext) => { ctx.consumeSpaces(); return tokText(ctx.consumeArg().tokens); };
const next = (ctx: MacroContext) => { ctx.consumeSpaces(); return ctx.future().text; };
const star = (ctx: MacroContext) => { if (ctx.future().text === '*') { ctx.popToken(); return true; } return false; };

/** Tokens up to the matching close (e.g. "(" … ")"), braces and nesting respected; the delimiters are dropped. */
function delimited(ctx: MacroContext, open: string, close: string): string {
  ctx.popToken();   // the opening one
  const toks: { text: string }[] = [];
  let depth = 0, braces = 0;
  for (;;) {
    const t = ctx.future();
    if (t.text === 'EOF') break;
    if (braces === 0 && t.text === close && depth === 0) { ctx.popToken(); break; }
    if (t.text === '{') braces++;
    else if (t.text === '}') braces--;
    else if (braces === 0 && open !== close && t.text === open) depth++;
    else if (braces === 0 && open !== close && t.text === close) depth--;
    toks.push(ctx.popToken());
  }
  return tokText(toks.reverse());
}

/** An optional [ … ] argument, or null. */
function optional(ctx: MacroContext): string | null {
  if (next(ctx) !== '[') return null;
  return delimited(ctx, '[', ']');
}

/** "#1 … #n" all present, so KaTeX takes n arguments even when some are unused. */
function withAllArgs(body: string, n: number, from = 1): string {
  let out = body;
  for (let k = from; k <= n; k++) if (!new RegExp(`#${k}(?!\\d)`).test(body)) out += `\\@secondoftwo{#${k}}{}`;
  return out;
}

/** A definition as a KaTeX macro (a string, or a function for an optional first argument). */
export function toMacro(d: Pick<Definition, 'nargs' | 'opt' | 'body'>): Macro {
  if (d.opt == null) return withAllArgs(d.body, d.nargs);
  const n = d.nargs;
  return (ctx: MacroContext) => {
    const o = optional(ctx) ?? d.opt!;
    // #1 is the optional argument; #2… become #1… for KaTeX to read
    const body = d.body.replace(/#(\d)/g, (m, k: string) => (k === '1' ? o : `#${Number(k) - 1}`));
    return withAllArgs(body, n - 1);
  };
}

/* ------------------------------------------------------------------ the physics package */

/** \qty( … ), \qty[ … ], \qty| … |, \qty{ … }: brackets that grow with their content. */
function quantity(ctx: MacroContext): string {
  const t = next(ctx);
  if (t === '(') return `\\left( ${delimited(ctx, '(', ')')} \\right)`;
  if (t === '[') return `\\left[ ${delimited(ctx, '[', ']')} \\right]`;
  if (t === '|') return `\\left| ${delimited(ctx, '|', '|')} \\right|`;
  if (t === '{') return `\\left\\{ ${arg(ctx)} \\right\\}`;
  return '';
}

/** \mqty( … ) and friends: a matrix (rows with \\, columns with &). */
const MATRIX_KIND: Record<string, string> = { '(': 'pmatrix', '[': 'bmatrix', '|': 'vmatrix' };
const CLOSING: Record<string, string> = { '(': ')', '[': ']', '|': '|' };

const matrix = (env: string) => (ctx: MacroContext) => {
  const t = next(ctx);
  const kind = MATRIX_KIND[t];
  if (env === 'matrix' && kind) return `\\begin{${kind}} ${delimited(ctx, t, CLOSING[t])} \\end{${kind}}`;
  return `\\begin{${env}} ${arg(ctx)} \\end{${env}}`;
};

/** \dv{f}{x}, \dv[n]{f}{x}, \dv{x}; \pdv also \pdv{f}{x}{y} (mixed). */
const derivative = (d: string) => (ctx: MacroContext) => {
  star(ctx);
  const n = optional(ctx);
  const p = n ? `^{${n}}` : '';
  const a = arg(ctx);
  if (next(ctx) !== '{') return `\\frac{${d}${p}}{${d} ${a}${p}}`;
  const b = arg(ctx);
  if (d === '\\partial' && next(ctx) === '{') return `\\frac{${d}^{2} ${a}}{${d} ${b}\\, ${d} ${arg(ctx)}}`;
  return `\\frac{${d}${p} ${a}}{${d} ${b}${p}}`;
};

const sized = (l: string, r: string) => (ctx: MacroContext) => (star(ctx) ? `${l.replace('\\left', '')} #1 ${r.replace('\\right', '')}` : `${l} #1 ${r}`);

export const PHYSICS: Record<string, Macro> = {
  // vectors
  '\\vb': (ctx) => (star(ctx) ? '\\boldsymbol{#1}' : '\\boldsymbol{\\mathbf{#1}}'),
  '\\va': (ctx) => (star(ctx) ? '\\vec{#1}' : '\\vec{\\mathrm{#1}}'),
  '\\vu': (ctx) => (star(ctx) ? '\\hat{\\boldsymbol{#1}}' : '\\hat{\\boldsymbol{\\mathbf{#1}}}'),
  '\\vdot': '\\boldsymbol{\\cdot}',
  '\\cross': '\\boldsymbol{\\times}',
  '\\grad': '\\boldsymbol{\\nabla}',
  '\\divergence': '\\boldsymbol{\\nabla}\\cdot',
  '\\curl': '\\boldsymbol{\\nabla}\\times',
  '\\laplacian': '\\nabla^2',
  // brackets
  '\\qty': quantity,
  '\\pqty': '\\left( #1 \\right)',
  '\\bqty': '\\left[ #1 \\right]',
  '\\Bqty': '\\left\\{ #1 \\right\\}',
  '\\vqty': '\\left\\lvert #1 \\right\\rvert',
  '\\abs': sized('\\left\\lvert', '\\right\\rvert'),
  '\\norm': sized('\\left\\lVert', '\\right\\rVert'),
  '\\order': '\\mathcal{O}\\left( #1 \\right)',
  '\\eval': (ctx) => `\\left. ${arg(ctx)} \\right\\rvert`,
  '\\comm': '\\left[ #1, #2 \\right]',
  '\\commutator': '\\left[ #1, #2 \\right]',
  '\\acomm': '\\left\\{ #1, #2 \\right\\}',
  '\\anticommutator': '\\left\\{ #1, #2 \\right\\}',
  // derivatives
  '\\dd': (ctx) => { const n = optional(ctx); return `\\mathop{}\\!\\mathrm{d}${n ? `^{${n}}` : ''}`; },
  '\\dv': derivative('\\mathrm{d}'),
  '\\pdv': derivative('\\partial'),
  // Dirac notation (\ket, \bra, \braket{a|b} are KaTeX's own)
  '\\braket': (ctx) => { const a = arg(ctx); return next(ctx) === '{' ? `\\left\\langle ${a} \\middle| ${arg(ctx)} \\right\\rangle` : `\\mathinner{\\langle{${a}}\\rangle}`; },
  '\\ip': '\\left\\langle #1 \\middle| #2 \\right\\rangle',
  '\\innerproduct': '\\left\\langle #1 \\middle| #2 \\right\\rangle',
  '\\expval': (ctx) => { const a = arg(ctx); return next(ctx) === '{' ? (() => { const b = arg(ctx); return `\\left\\langle ${b} \\middle| ${a} \\middle| ${b} \\right\\rangle`; })() : `\\left\\langle ${a} \\right\\rangle`; },
  '\\ev': (ctx) => (PHYSICS['\\expval'] as (c: MacroContext) => string)(ctx),
  '\\mel': '\\left\\langle #1 \\middle| #2 \\middle| #3 \\right\\rangle',
  '\\matrixel': '\\left\\langle #1 \\middle| #2 \\middle| #3 \\right\\rangle',
  '\\dyad': (ctx) => { const a = arg(ctx); const b = next(ctx) === '{' ? arg(ctx) : a; return `\\left\\lvert ${a} \\middle\\rangle\\!\\middle\\langle ${b} \\right\\rvert`; },
  '\\outerproduct': (ctx) => (PHYSICS['\\dyad'] as (c: MacroContext) => string)(ctx),
  '\\op': (ctx) => (PHYSICS['\\dyad'] as (c: MacroContext) => string)(ctx),
  // matrices
  '\\mqty': matrix('matrix'),
  '\\pmqty': matrix('pmatrix'),
  '\\bmqty': matrix('bmatrix'),
  '\\vmqty': matrix('vmatrix'),
  // operators and text
  '\\tr': '\\operatorname{tr}',
  '\\Tr': '\\operatorname{Tr}',
  '\\trace': '\\operatorname{tr}',
  '\\rank': '\\operatorname{rank}',
  '\\erf': '\\operatorname{erf}',
  '\\Res': '\\operatorname{Res}',
  '\\pv': '\\mathcal{P}',
  '\\qq': '\\quad\\text{#1}\\quad',
};
// (\div is left alone: it is KaTeX's ÷, and the package's divergence is \divergence here.)

/** Names of the physics commands, for Settings and the docs. */
export const PHYSICS_NAMES = Object.keys(PHYSICS);

/* ------------------------------------------------------------------ putting it together */

export interface BaseMacros { macros: Macros; names: string[]; problems: string[] }

let cached: { key: string | null; value: BaseMacros | null } = { key: null, value: null };

/**
 * The macros every note starts from: the physics commands (if on) and the definitions in
 * Settings. Returns {macros, names, problems} (problems: lines that are not definitions).
 */
export function baseMacros({ physics = true, text = '' } = {}): BaseMacros {
  const key = `${physics}\u0000${text}`;
  if (cached.key === key) return cached.value!;
  const macros: Macros = physics ? { ...PHYSICS } : {};
  const { defs, rest } = parseDefinitions(text || '');
  for (const d of defs) macros[d.name] = toMacro(d);
  const problems = rest.split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('%'));
  const value = { macros, names: defs.map((d) => d.name), problems };
  cached = { key, value };
  return value;
}

/** The macros of one note: the base ones plus the note's own definitions (a \providecommand
 *  never replaces one that exists). */
export function withDefinitions(base: Macros, defs: Definition[]): Macros {
  if (!defs.length) return base;
  const out = { ...base };
  for (const d of defs) if (!(d.provide && d.name in out)) out[d.name] = toMacro(d);
  return out;
}
