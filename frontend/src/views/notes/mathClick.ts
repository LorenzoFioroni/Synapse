// Where in a formula's TeX a click on its rendering landed.
//
// KaTeX keeps no link from the drawn symbols back to the source, so a click on a rendered
// equation used to put the cursor at the start or end of the whole formula. Here the source is
// cut into tokens that draw something (letters, digits, operators, \alpha, \sum…) and matched,
// in order, against the characters KaTeX drew: the clicked character is looked up among the
// source tokens that draw the same character, preferring the one at about the same place in
// the order. KaTeX draws a fraction's denominator before its numerator and a subscript before
// a superscript, so order alone is not enough; the characters themselves settle most cases.
// DOM-free (tested with Node); livePreview.ts collects the drawn characters.

const SYMBOLS: Record<string, string> = {
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ϵ', varepsilon: 'ε', zeta: 'ζ', eta: 'η', theta: 'θ',
  vartheta: 'ϑ', iota: 'ι', kappa: 'κ', lambda: 'λ', mu: 'μ', nu: 'ν', xi: 'ξ', pi: 'π', varpi: 'ϖ', rho: 'ρ',
  varrho: 'ϱ', sigma: 'σ', varsigma: 'ς', tau: 'τ', upsilon: 'υ', phi: 'ϕ', varphi: 'φ', chi: 'χ', psi: 'ψ',
  omega: 'ω', Gamma: 'Γ', Delta: 'Δ', Theta: 'Θ', Lambda: 'Λ', Xi: 'Ξ', Pi: 'Π', Sigma: 'Σ', Upsilon: 'Υ',
  Phi: 'Φ', Psi: 'Ψ', Omega: 'Ω', sum: '∑', prod: '∏', int: '∫', iint: '∬', oint: '∮', partial: '∂',
  infty: '∞', nabla: '∇', hbar: 'ℏ', ell: 'ℓ', cdot: '⋅', cdots: '⋯', ldots: '…', dots: '…', times: '×',
  pm: '±', mp: '∓', le: '≤', leq: '≤', ge: '≥', geq: '≥', neq: '≠', ne: '≠', approx: '≈', simeq: '≃',
  sim: '∼', equiv: '≡', propto: '∝', to: '→', rightarrow: '→', leftarrow: '←', Rightarrow: '⇒',
  Leftrightarrow: '⇔', longrightarrow: '⟶', mapsto: '↦', in: '∈', notin: '∉', subset: '⊂', cup: '∪',
  cap: '∩', langle: '⟨', rangle: '⟩', dagger: '†', prime: '′', star: '⋆', ast: '∗', circ: '∘',
  otimes: '⊗', oplus: '⊕', lesssim: '≲', gtrsim: '≳', ll: '≪', gg: '≫', forall: '∀', exists: '∃',
  mid: '∣', vert: '∣', Vert: '∥', lvert: '∣', rvert: '∣', lbrace: '{', rbrace: '}', '{': '{', '}': '}',
  '|': '∥', '%': '%', '$': '$', '#': '#', '&': '&', _: '_',
};
// commands that draw letters of their own name (\sin → "sin")
const NAMED = new Set(['sin', 'cos', 'tan', 'cot', 'sec', 'csc', 'sinh', 'cosh', 'tanh', 'log', 'ln', 'exp', 'lim',
  'max', 'min', 'sup', 'inf', 'det', 'dim', 'ker', 'arg', 'deg', 'gcd', 'Pr', 'Re', 'Im', 'tr', 'Tr']);
// characters KaTeX draws differently from how they are typed
const DRAWN: Record<string, string> = { '-': '−', '*': '∗', "'": '′', '<': '<', '>': '>' };
// arguments that are names, not formula: nothing in them is drawn as typed (or at all)
const SKIP_ARG = new Set(['begin', 'end', 'label', 'tag', 'eqref', 'ref', 'color', 'textcolor', 'colorbox',
  'operatorname', 'hspace', 'vspace', 'rule']);

/**
 * The tokens of a TeX string that draw something: {from, to, ch} with ch the character drawn
 * (or null when unknown: still counts in the order).
 */
export interface TexToken { from: number; to: number; ch: string | null }

export function texTokens(tex: string): TexToken[] {
  const out: TexToken[] = [];
  let i = 0;
  while (i < tex.length) {
    const c = tex[i];
    if (c === '\\') {
      const m = /^\\([A-Za-z]+|.)/.exec(tex.slice(i));
      if (!m) break;
      const name = m[1];
      const end = i + m[0].length;
      if (SKIP_ARG.has(name)) {   // skip its {argument} as a whole (\tag's text is drawn, but in the number column)
        let j = end;
        while (tex[j] === ' ' || tex[j] === '*') j++;
        if (tex[j] === '{') { let d = 0; for (; j < tex.length; j++) { if (tex[j] === '{') d++; else if (tex[j] === '}' && --d === 0) { j++; break; } } }
        if (name === 'operatorname') {   // \operatorname{Tr}: its letters are drawn
          const arg = tex.slice(end, j).replace(/^[\s*]*\{|\}$/g, '');
          const at = tex.indexOf(arg, end);
          for (let k = 0; k < arg.length; k++) if (/\S/.test(arg[k])) out.push({ from: at + k, to: at + k + 1, ch: arg[k] });
        }
        i = j;
        continue;
      }
      if (name === '\\') { i = end; continue; }   // row break
      if (NAMED.has(name)) { for (let k = 0; k < name.length; k++) out.push({ from: i, to: end, ch: name[k] }); i = end; continue; }
      if (SYMBOLS[name]) { out.push({ from: i, to: end, ch: SYMBOLS[name] }); i = end; continue; }
      // \frac, \left, \mathrm, \quad, \, … draw nothing by themselves (their arguments are read on)
      i = end;
      continue;
    }
    if (/[\s{}^_&~]/.test(c)) { i++; continue; }
    out.push({ from: i, to: i + 1, ch: DRAWN[c] || c });
    i++;
  }
  return out;
}

/**
 * Offset in ``tex`` for a click on the ``index``-th of the ``drawn`` characters (in DOM order),
 * ``after`` when the click was on its right half.
 */
export function clickOffset(tex: string, drawn: string[], index: number, after = false): number {
  const toks = texTokens(tex);
  if (!toks.length) return after ? tex.length : 0;
  const ch = drawn[index];
  const want = drawn.length > 1 ? (index / (drawn.length - 1)) * (toks.length - 1) : 0;
  let best = -1, bestDist = Infinity;
  toks.forEach((t, k) => {
    if (t.ch !== ch) return;
    // prefer the same occurrence count: the n-th "x" drawn is likely the n-th "x" typed
    const dist = Math.abs(k - want);
    if (dist < bestDist) { best = k; bestDist = dist; }
  });
  const nth = drawn.slice(0, index).filter((c) => c === ch).length;
  const same = toks.filter((t) => t.ch === ch);
  if (same.length === drawn.filter((c) => c === ch).length && same[nth]) best = toks.indexOf(same[nth]);
  if (best < 0) best = Math.max(0, Math.min(toks.length - 1, Math.round(want)));
  const t = toks[best];
  return after ? t.to : t.from;
}
