// Text for pdfLaTeX: escaping, and what UTF-8 input with T1 fonts cannot typeset on its own.
// The exported source keeps the note's characters; the preamble declares those LaTeX lacks
// (tested in tests/js/latex.test.mjs, which also compiles with a local pdflatex when present).

/** Code points pdfLaTeX's utf8 input defines with T1/TS1 fonts (measured on TeX Live 2024). */
const NATIVE: [number, number][] = [
  [0x00, 0x7f], [0xa0, 0x125], [0x128, 0x137], [0x139, 0x13e], [0x141, 0x148], [0x14a, 0x165],
  [0x168, 0x17e], [0x192, 0x192], [0x1c4, 0x1d4], [0x1e2, 0x1e3], [0x1e6, 0x1eb], [0x1f0, 0x1f0],
  [0x1f4, 0x1f5], [0x218, 0x21b], [0x232, 0x233], [0x237, 0x237], [0x2010, 0x2016], [0x2018, 0x201a],
  [0x201c, 0x201e], [0x2020, 0x2022], [0x2026, 0x2026], [0x2030, 0x2031], [0x2039, 0x203b],
  [0x20ac, 0x20ac], [0x2103, 0x2103], [0x2116, 0x2117], [0x2122, 0x2122], [0x2126, 0x2127],
  [0x2190, 0x2193], [0x25e6, 0x25e6], [0x27e8, 0x27e9],
];
const native = (code: number) => NATIVE.some(([a, b]) => code >= a && code <= b);

// Box drawing for code blocks: pmboxdraw draws most of U+2500–U+259F at the width of a monospace
// character; the shapes it lacks fall back to the nearest one it has.
const BOX = (code: number) => code >= 0x2500 && code <= 0x259f;
const BOX_ALIAS: Record<number, string> = {
  0x2504: '─', 0x2508: '─', 0x254c: '─', 0x2505: '━', 0x2509: '━', 0x254d: '━',
  0x2506: '│', 0x250a: '│', 0x254e: '│', 0x2507: '┃', 0x250b: '┃', 0x254f: '┃',
  0x2525: '┤', 0x2526: '┤', 0x2527: '┤', 0x2528: '┤', 0x2529: '┤', 0x252a: '┤', 0x252b: '┫',
  0x256d: '┌', 0x256e: '┐', 0x256f: '┘', 0x2570: '└', 0x2571: '/', 0x2572: '\\textbackslash{}', 0x2573: 'X',
};

/** Symbols written in formulas (KaTeX accepts them, LaTeX does not) and their math commands. */
const MATH: Record<string, string> = {
  'α': '\\alpha', 'β': '\\beta', 'γ': '\\gamma', 'δ': '\\delta', 'ε': '\\varepsilon', 'ϵ': '\\epsilon',
  'ζ': '\\zeta', 'η': '\\eta', 'θ': '\\theta', 'ϑ': '\\vartheta', 'ι': '\\iota', 'κ': '\\kappa',
  'λ': '\\lambda', 'μ': '\\mu', 'ν': '\\nu', 'ξ': '\\xi', 'π': '\\pi', 'ϖ': '\\varpi', 'ρ': '\\rho',
  'ϱ': '\\varrho', 'σ': '\\sigma', 'ς': '\\varsigma', 'τ': '\\tau', 'υ': '\\upsilon', 'φ': '\\varphi',
  'ϕ': '\\phi', 'χ': '\\chi', 'ψ': '\\psi', 'ω': '\\omega', 'Γ': '\\Gamma', 'Δ': '\\Delta',
  'Θ': '\\Theta', 'Λ': '\\Lambda', 'Ξ': '\\Xi', 'Π': '\\Pi', 'Σ': '\\Sigma', 'Υ': '\\Upsilon',
  'Φ': '\\Phi', 'Ψ': '\\Psi', 'Ω': '\\Omega', 'µ': '\\mu',
  '≈': '\\approx', '≃': '\\simeq', '≅': '\\cong', '∼': '\\sim', '≠': '\\neq', '≡': '\\equiv',
  '≤': '\\leq', '≥': '\\geq', '≲': '\\lesssim', '≳': '\\gtrsim', '≪': '\\ll', '≫': '\\gg',
  '∝': '\\propto', '≺': '\\prec', '≻': '\\succ', '∞': '\\infty', '∂': '\\partial', '∇': '\\nabla',
  '∑': '\\sum', '∏': '\\prod', '∫': '\\int', '∮': '\\oint', '√': '\\surd', '∈': '\\in', '∉': '\\notin',
  '∋': '\\ni', '⊂': '\\subset', '⊃': '\\supset', '⊆': '\\subseteq', '⊇': '\\supseteq', '∪': '\\cup',
  '∩': '\\cap', '∖': '\\setminus', '∅': '\\emptyset', '∀': '\\forall', '∃': '\\exists', '∄': '\\nexists',
  '∧': '\\wedge', '∨': '\\vee', '¬': '\\neg', '⊗': '\\otimes', '⊕': '\\oplus', '⊙': '\\odot',
  '⊥': '\\perp', '∥': '\\parallel', '∣': '\\mid', '⋅': '\\cdot', '·': '\\cdot', '∘': '\\circ',
  '∗': '\\ast', '×': '\\times', '÷': '\\div', '±': '\\pm', '∓': '\\mp', '−': '-', '†': '\\dagger',
  '‡': '\\ddagger', '…': '\\dots', '⋯': '\\cdots', '⋮': '\\vdots', '⋱': '\\ddots', '°': '^{\\circ}',
  '′': "'", '″': "''", 'ℏ': '\\hbar', 'ℓ': '\\ell', 'ℜ': '\\Re', 'ℑ': '\\Im', 'ℵ': '\\aleph',
  'ℝ': '\\mathbb{R}', 'ℂ': '\\mathbb{C}', 'ℕ': '\\mathbb{N}', 'ℤ': '\\mathbb{Z}', 'ℚ': '\\mathbb{Q}',
  '←': '\\leftarrow', '→': '\\rightarrow', '↑': '\\uparrow', '↓': '\\downarrow', '↔': '\\leftrightarrow',
  '⇐': '\\Leftarrow', '⇒': '\\Rightarrow', '⇔': '\\Leftrightarrow', '↦': '\\mapsto',
  '⟶': '\\longrightarrow', '⟹': '\\Longrightarrow', '⟨': '\\langle', '⟩': '\\rangle', '∠': '\\angle',
  '⊤': '\\top', '⊢': '\\vdash', '⊨': '\\models', '□': '\\square', '■': '\\blacksquare',
  '★': '\\bigstar', '✓': '\\checkmark', '♯': '\\sharp', '♭': '\\flat',
};
const SUPER = '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ', SUB = '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎';
const SCRIPT_TEXT = '0123456789+-=()ni';
/** The same symbols in running text, where they are not native. */
const TEXT: Record<string, string> = {
  '\u2002': '\\enspace{}', '\u2003': '\\quad{}', '\u2007': '\\enspace{}', '\u2009': '\\,', '\u200a': '\\,',
  '\u200b': '\\hspace{0pt}', '\u202f': '\\,', '\u2060': '{}', '\ufeff': '{}', '\u201b': "'", '\u201f': '"',
  '\u2212': '\\textminus{}', '✗': '\\ensuremath{\\times}', '″': '\\ensuremath{^{\\prime\\prime}}', '′': '\\ensuremath{^{\\prime}}',
};

/** Escape plain text for LaTeX (T1 fonts, so <, >, | and " print as themselves). */
export function texEscape(text: string): string {
  const escapes: Record<string, string> = { '\\': '\\textbackslash{}', '{': '\\{', '}': '\\}', '#': '\\#', '$': '\\$', '%': '\\%', '&': '\\&', '_': '\\_', '^': '\\textasciicircum{}', '~': '\\textasciitilde{}' };
  return text.normalize('NFC').replace(/[\\{}#$%&_^~]/g, c => escapes[c])
    // T1 ligatures that would change what was written: << >> ,, ?` !`
    .replace(/([<>,])(?=\1)/g, '$1{}').replace(/([?!])(?=`)/g, '$1{}');
}

/** A formula with the Unicode symbols KaTeX accepts written as LaTeX commands. Text arguments
 *  (\text{…}, \mbox{…}) keep their characters, which the preamble declares for text. */
export function mathUnicode(tex: string): string {
  if (!/[^\x00-\x7f]/.test(tex)) return tex;
  let out = '';
  const groups: boolean[] = [];   // for each open brace: whether it is a text argument
  for (let i = 0; i < tex.length;) {
    const rest = tex.slice(i);
    const command = /^\\(?:[A-Za-z]+|.)/s.exec(rest)?.[0];
    if (command) {
      const text = /^\\(?:text(?:rm|it|bf|up|sf|tt|normal)?|mbox)$/.test(command) && tex[i + command.length] === '{';
      out += command + (text ? '{' : ''); i += command.length + (text ? 1 : 0);
      if (text) groups.push(true);
      continue;
    }
    const c = String.fromCodePoint(tex.codePointAt(i)!);
    i += c.length;
    if (c === '{') groups.push(groups[groups.length - 1] ?? false);
    else if (c === '}') groups.pop();
    if (c.charCodeAt(0) < 0x80 || groups[groups.length - 1]) { out += c; continue; }
    const s = SUPER.indexOf(c), b = SUB.indexOf(c), math = MATH[c.normalize('NFC')];
    // a command name must not run into a letter after it
    out += s >= 0 ? `^{${SCRIPT_TEXT[s]}}` : b >= 0 ? `_{${SCRIPT_TEXT[b]}}`
      : math ? (/[A-Za-z]$/.test(math) && /[A-Za-z]/.test(tex[i] || '') ? `${math} ` : math) : c;
  }
  return out;
}

/** What the preamble needs for the characters of ``texts``: pmboxdraw for box drawing, and
 *  newunicodechar declarations for the rest; ``missing`` lists characters with no rendering. */
export function unicodePreamble(texts: string[]): { preamble: string; missing: string[] } {
  const chars = new Set<string>();
  for (const text of texts) for (const c of text) if (!native(c.codePointAt(0)!)) chars.add(c);
  let box = false;
  const declarations: string[] = [], missing: string[] = [];
  for (const c of [...chars].sort()) {
    const code = c.codePointAt(0)!;
    if (BOX(code) && !BOX_ALIAS[code]) { box = true; continue; }
    let replacement = BOX_ALIAS[code] ?? TEXT[c];
    if (BOX_ALIAS[code]) box = true;
    const s = SUPER.indexOf(c), b = SUB.indexOf(c);
    if (replacement == null && s >= 0) replacement = `\\textsuperscript{${SCRIPT_TEXT[s]}}`;
    if (replacement == null && b >= 0) replacement = `\\textsubscript{${SCRIPT_TEXT[b]}}`;
    if (replacement == null && MATH[c]) replacement = `\\ensuremath{${MATH[c]}}`;
    if (replacement == null) {
      // a placeholder that names the character, so it can be found and replaced by hand
      replacement = `{\\ttfamily[U+${code.toString(16).toUpperCase().padStart(4, '0')}]}`;
      missing.push(c);
    }
    declarations.push(`\\newunicodechar{${c}}{${replacement}}`);
  }
  const preamble = (box ? '\\usepackage{pmboxdraw}\n' : '')
    + (declarations.length ? `\\usepackage{newunicodechar}\n${declarations.join('\n')}\n` : '');
  return { preamble, missing };
}
