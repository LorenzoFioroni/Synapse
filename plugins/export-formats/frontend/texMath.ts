// Display formulas as LaTeX. KaTeX draws environments LaTeX allows only at the top of a display
// (an align next to a \label inside $$ … $$) and line breaks anywhere; LaTeX needs aligned,
// gathered and a single \label per number. Numbers follow the note's rule
// (views/notes/equations.ts): a $$ formula is numbered when labelled, tagged or in "number all" mode.

const TOP = new Set(['equation', 'align', 'gather', 'alignat', 'flalign', 'multline']);
const NESTED: Record<string, string> = { align: 'aligned', flalign: 'aligned', gather: 'gathered', multline: 'gathered', alignat: 'alignedat' };
const LABEL = /\\label\s*\{([^{}]*)\}/g;
const TAG = /\\tag\*?\s*\{(?:[^{}]|\{[^{}]*\})*\}/g;
const NOTAG = /\\(?:notag|nonumber)(?![A-Za-z])/g;

/** The single environment a formula consists of, or null. */
function outerEnv(tex: string): { name: string; star: string; body: string } | null {
  const m = /^\s*\\begin\s*\{([A-Za-z]+)(\*?)\}([\s\S]*)\\end\s*\{\1\2\}\s*$/.exec(tex);
  if (!m) return null;
  let depth = 0;   // "\begin{a}…\end{a} + \begin{a}…\end{a}" is not one environment
  for (const t of m[3].matchAll(/\\(begin|end)\s*\{([A-Za-z]+\*?)\}/g)) if ((depth += t[1] === 'begin' ? 1 : -1) < 0) return null;
  return { name: m[1], star: m[2], body: m[3] };
}

/** The rows of a formula (split at top-level \\) and whether a top-level & aligns them. */
function rowsOf(body: string): { rows: string[]; aligned: boolean } {
  const rows: string[] = [];
  let depth = 0, start = 0, aligned = false;
  for (let i = 0; i < body.length; i++) {
    const c = body[i];
    if (c === '\\') {
      if (body.startsWith('\\begin', i)) { depth++; i += 5; continue; }
      if (body.startsWith('\\end', i)) { depth--; i += 3; continue; }
      if (body[i + 1] === '\\' && depth === 0) { rows.push(body.slice(start, i)); start = i + 2; }
      i++;
    } else if (c === '{') depth++;
    else if (c === '}') depth--;
    else if (c === '&' && depth === 0) aligned = true;
  }
  rows.push(body.slice(start));
  return { rows, aligned };
}

/** Top-level environments inside a formula become their nested forms (align → aligned). */
const nest = (tex: string) => tex.replace(/\\(begin|end)\s*\{(equation|align|gather|alignat|flalign|multline)\*?\}/g,
  (_, kind: string, name: string) => (name === 'equation' ? '' : `\\${kind}{${NESTED[name]}}`));

/** One \label per number: the others become aliases of the first ([alias, label]). */
function oneLabel(row: string, aliases: [string, string][]): string {
  let first: string | null = null;
  return row.replace(LABEL, (all, name: string) => {
    if (first == null) { first = name.trim(); return all; }
    aliases.push([name.trim(), first]); return '';
  });
}

/**
 * A display formula as a LaTeX environment, with the note's own label names; ``aliases`` maps
 * further labels of one number to the label that is kept.
 */
export function displayMath(source: string, numberAll: boolean): { tex: string; aliases: [string, string][] } {
  const aliases: [string, string][] = [];
  const tex = source.trim();
  if (!tex) return { tex: '', aliases };
  const env = outerEnv(tex);
  if (env && TOP.has(env.name) && env.name !== 'equation') {
    // numbered row by row as in LaTeX: keep the environment, nest what is inside
    const body = rowsOf(env.body).rows.map(row => oneLabel(nest(row), aliases)).join('\\\\');
    return { tex: `\\begin{${env.name}${env.star}}${body}\\end{${env.name}${env.star}}`, aliases };
  }
  // anything else is one equation: its labels, tag and \notag apply to the whole formula
  let body = env?.name === 'equation' ? env.body : tex;
  const auto = env?.name === 'equation' ? !env.star : numberAll && !env?.star;
  const labels: string[] = [];
  body = body.replace(LABEL, (_, name: string) => { labels.push(name.trim()); return ''; });
  const tag = body.match(TAG)?.[0] ?? null;
  const notag = new RegExp(NOTAG.source).test(body);
  body = nest(body.replace(TAG, '').replace(NOTAG, '')).trim();
  const { rows, aligned } = rowsOf(body);
  if (rows.filter((row, i) => i < rows.length - 1 || row.trim()).length > 1) {
    const name = aligned ? 'aligned' : 'gathered';
    body = `\\begin{${name}}\n${body}\n\\end{${name}}`;
  }
  if (!tag && (notag || (!auto && !labels.length))) return { tex: `\\[\n${body}\n\\]`, aliases };
  for (const name of labels.slice(1)) aliases.push([name, labels[0]]);
  return { tex: `\\begin{equation}\n${body}${tag ? `\n${tag}` : ''}${labels.length ? `\n\\label{${labels[0]}}` : ''}\n\\end{equation}`, aliases };
}

/** Labels and references in TeX renamed with ``label`` (a comma-separated \cref list name by name). */
export function mapLabels(tex: string, label: (name: string) => string): string {
  return tex.replace(/\\(label|eqref|ref|[cC]ref|pageref)\s*\{([^{}]*)\}/g,
    (_, command: string, names: string) => `\\${command}{${names.split(',').map(n => label(n.trim())).join(',')}}`);
}

/** Packages that commands KaTeX knows need in LaTeX, and KaTeX's own shorthands. */
const PACKAGES: [RegExp, string][] = [
  [/\\[bx]?cancel(?:to)?(?![A-Za-z])/, '\\usepackage{cancel}'],
  [/\\bm(?![A-Za-z])/, '\\usepackage{bm}'],
  [/\\mathscr(?![A-Za-z])/, '\\usepackage{mathrsfs}'],
  [/\\(?:llbracket|rrbracket)(?![A-Za-z])/, '\\usepackage{stmaryrd}'],
];
const SHORTHANDS: Record<string, string> = { R: '\\mathbb{R}', N: '\\mathbb{N}', Z: '\\mathbb{Z}', C: '\\mathbb{C}', Q: '\\mathbb{Q}' };

/** What the preamble needs for the formulas ``tex`` uses. */
export function mathPreamble(tex: string): string {
  const lines = PACKAGES.filter(([re]) => re.test(tex)).map(([, line]) => line);
  for (const [name, body] of Object.entries(SHORTHANDS)) if (new RegExp(`\\\\${name}(?![A-Za-z])`).test(tex)) lines.push(`\\providecommand{\\${name}}{${body}}`);
  return lines.map(line => `${line}\n`).join('');
}
