"""Text helpers: canonicalisation, HTML->LaTeX, casing, names, identifiers."""
from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz

from . import latex

# --------------------------------------------------------------------------
# Canonical forms (for comparisons only — never written to Zotero)
# --------------------------------------------------------------------------
_FOLD = str.maketrans({"ß": "ss", "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe",
                       "Œ": "oe", "ł": "l", "Ł": "l", "ı": "i", "đ": "d", "Đ": "d",
                       "ð": "d", "þ": "th", "ŋ": "ng", "–": "-", "—": "-"})


def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).translate(_FOLD)


def plain(s: str) -> str:
    """LaTeX/HTML-free Unicode rendering of a field value."""
    s = s or ""
    if "<" in s and re.search(r"</?[A-Za-z][\w:-]*[^>]*>", s):
        s = re.sub(r"</?[A-Za-z][\w:-]*[^>]*>", "", s)
    if "&" in s:
        s = html.unescape(s)
    return re.sub(r"\s+", " ", latex.to_unicode(s)).strip()


def canon(s: str) -> str:
    """Case/accent/punctuation/LaTeX-insensitive key."""
    s = strip_accents(plain(s)).casefold()
    s = re.sub(r"[\W_]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


STOP = set("a an the of and or in on for to with by from at as via is are be its into "
           "de la le les des du et und der die das von zu im".split())


def title_key(s: str) -> str:
    toks = [t for t in canon(s).split() if t not in STOP]
    return " ".join(toks)


def sim(a: str, b: str) -> float:
    """Similarity 0..100 between two field values (canonical, order-sensitive)."""
    a, b = canon(a), canon(b)
    if not a or not b:
        return 0.0
    return fuzz.ratio(a, b)


def loose_equal(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return canon(a) == canon(b)
    return a == b


def same_rendering(a: str, b: str) -> bool:
    """True if a and b render to exactly the same text (case-sensitive)."""
    return re.sub(r"\s+", " ", plain(a).replace(" ", " ")).strip() == \
        re.sub(r"\s+", " ", plain(b).replace(" ", " ")).strip()


# --------------------------------------------------------------------------
# Whitespace / HTML
# --------------------------------------------------------------------------

def collapse_ws(s: str, keep_newlines: bool = False) -> str:
    if s is None:
        return s
    if keep_newlines:
        lines = [re.sub(r"[ \t  -   　]+", " ", ln).strip() for ln in s.splitlines()]
        out, blank = [], False
        for ln in lines:
            if not ln:
                if not blank and out:
                    out.append("")
                blank = True
            else:
                out.append(ln)
                blank = False
        return "\n".join(out).strip()
    return re.sub(r"[\s -   　]+", " ", s).strip()


_KNOWN_TAGS = {"i", "em", "b", "strong", "sub", "sup", "sc", "span", "italic", "bold",
               "scp", "p", "br", "title", "math", "inline-formula", "tex-math", "u",
               "underline", "monospace", "tt", "small", "roman", "a", "div", "font",
               "alternatives", "mi", "mo", "mn", "mrow", "msub", "msup", "msubsup",
               "mtext", "mfrac", "msqrt", "mover", "munder", "mstyle", "semantics",
               "annotation", "mspace", "mfenced", "mpadded", "mroot", "mtable", "mtr", "mtd",
               "inline-graphic", "styled-content", "named-content", "fn", "xref", "label"}
_TAG_RE = re.compile(r"<(/?)([A-Za-z][\w-]*(?::[\w-]+)?)((?:\s+[^<>]*?)?)\s*(/?)>")


def has_markup(s: str) -> bool:
    if not s:
        return False
    for m in _TAG_RE.finditer(s):
        if m.group(2).split(":")[-1].lower() in _KNOWN_TAGS:
            return True
    return bool(re.search(r"&(#\d+|#x[0-9a-fA-F]+|[A-Za-z]{2,8});", s))


def html_to_latex(s: str, *, style: str = "braced", escape_specials: bool = True) -> tuple[str, bool]:
    """Convert (JATS/Zotero rich-text/HTML) markup to LaTeX.

    Returns (text, complex) where complex=True when MathML or unknown structure
    was flattened (the caller should lower its confidence)."""
    if not s:
        return s, False
    complex_ = False
    # parse into a tree
    root: list = []
    stack: list[tuple[str, dict, list]] = [("root", {}, root)]
    pos = 0
    for m in _TAG_RE.finditer(s):
        name = m.group(2).split(":")[-1].lower()
        if name not in _KNOWN_TAGS:
            continue
        if m.start() > pos:
            stack[-1][2].append(s[pos:m.start()])
        pos = m.end()
        closing, attrs, selfclose = m.group(1), m.group(3) or "", m.group(4)
        if name in ("mi", "mo", "mn", "mrow", "msub", "msup", "math", "mfrac", "msqrt", "semantics", "annotation", "alternatives", "tex-math", "inline-formula", "msubsup", "mtext"):
            complex_ = True
        if closing:
            # pop to matching
            for k in range(len(stack) - 1, 0, -1):
                if stack[k][0] == name:
                    while len(stack) > k:
                        stack.pop()
                    break
            continue
        if selfclose or name == "br":
            stack[-1][2].append(("br", {}, []) if name == "br" else (name, {}, []))
            continue
        node = (name, {"attrs": attrs}, [])
        stack[-1][2].append(node)
        stack.append(node)
    if pos < len(s):
        stack[-1][2].append(s[pos:])

    def text_of(children) -> str:
        out = []
        for c in children:
            out.append(html.unescape(c) if isinstance(c, str) else text_of(c[2]))
        return "".join(out)

    def math_content(children) -> str:
        t = collapse_ws(text_of(children))
        m = latex.to_latex(t, style=style, escape_specials=False)
        # strip $ signs produced by to_latex: we are already in math
        m = m.replace("$", "")
        if re.fullmatch(r"[A-Za-z]{2,}", m):
            m = "\\mathrm{%s}" % m
        return m

    def render(children) -> str:
        out = []
        for c in children:
            if isinstance(c, str):
                out.append(latex.to_latex(html.unescape(c), style=style, escape_specials=escape_specials))
                continue
            name, meta, kids = c
            if name in ("i", "em", "italic"):
                out.append("\\textit{%s}" % render(kids))
            elif name in ("b", "strong", "bold"):
                out.append("\\textbf{%s}" % render(kids))
            elif name in ("sc", "scp"):
                out.append("\\textsc{%s}" % render(kids))
            elif name == "sub":
                out.append("$_{%s}$" % math_content(kids))
            elif name == "sup":
                out.append("$^{%s}$" % math_content(kids))
            elif name == "span" and "nocase" in meta.get("attrs", ""):
                out.append("{%s}" % render(kids))
            elif name == "br":
                out.append(" ")
            elif name in ("tex-math",):
                out.append(text_of(kids))
            elif name in ("annotation",):
                pass
            else:
                out.append(render(kids))
        return "".join(out)

    res = render(root)
    res = res.replace("$$", "")  # merge adjacent math: $a$$b$ -> $ab$
    return collapse_ws(res), complex_


# --------------------------------------------------------------------------
# Case handling (LaTeX aware)
# --------------------------------------------------------------------------
_ACCENT_ATOM = re.compile(
    r"\{\\(?:[`'^~=.\"](?:\\[ij]|[A-Za-z])|[uvHckdbrt](?:\s[A-Za-z]|\{\\?[A-Za-z]\}|\\[ij])|"
    r"ss|o|O|ae|AE|oe|OE|aa|AA|l|L|i|j|dh|DH|th|TH|dj|DJ|ng|NG)\}")
_ATOM = re.compile(_ACCENT_ATOM.pattern + r"|[^\W\d_]|\d|.", re.S)
_SPECIAL_CASE = {"O": "o", "AE": "ae", "OE": "oe", "AA": "aa", "L": "l", "DH": "dh",
                 "TH": "th", "DJ": "dj", "NG": "ng"}
_SPECIAL_CASE_UP = {v: k for k, v in _SPECIAL_CASE.items()}


def _atoms(word: str) -> list[str] | None:
    """Split a word into letter atoms; None if the word contains LaTeX we must
    not touch (math, commands, protective braces)."""
    atoms = []
    for m in _ATOM.finditer(word):
        a = m.group(0)
        if a in ("$", "\\", "{", "}"):
            return None
        atoms.append(a)
    return atoms


def _atom_case(a: str, upper: bool) -> str:
    if len(a) == 1:
        return a.upper() if upper else a.lower()
    m = re.fullmatch(r"\{\\([A-Za-z]{1,2})\}", a)
    if m:  # special letter
        name = m.group(1)
        if upper:
            return "{\\%s}" % _SPECIAL_CASE_UP.get(name, name)
        return "{\\%s}" % _SPECIAL_CASE.get(name, name)
    # accent atom: change case of the base letter (last ascii letter)
    m = re.search(r"([A-Za-z])(\}?\})$", a)
    if m and not a.endswith("\\i}") and not a.endswith("\\j}"):
        base = m.group(1)
        return a[:m.start(1)] + (base.upper() if upper else base.lower()) + a[m.end(1):]
    return a


def _atom_is_upper(a: str) -> bool | None:
    u = latex.to_unicode(a)
    if not u or not u[0].isalpha():
        return None
    return u[0].isupper()


def word_shape(word: str) -> str:
    """'lower', 'upper', 'cap' (Capitalised), 'mixed', 'other'."""
    atoms = _atoms(word)
    if atoms is None:
        return "protected"
    flags = [_atom_is_upper(a) for a in atoms]
    letters = [f for f in flags if f is not None]
    if not letters:
        return "other"
    if any(a.isdigit() for a in atoms):
        return "other"
    if all(letters):
        return "upper" if len(letters) > 1 else "cap"
    if not any(letters):
        return "lower"
    if letters[0] and not any(letters[1:]):
        return "cap"
    return "mixed"


def recase_word(word: str, mode: str) -> str:
    """mode: 'lower', 'cap' (first letter upper, rest lower)."""
    atoms = _atoms(word)
    if atoms is None:
        return word
    out, first = [], True
    for a in atoms:
        if _atom_is_upper(a) is None:
            out.append(a)
            if a in "-/'’":
                pass
            continue
        out.append(_atom_case(a, upper=(mode == "cap" and first)))
        first = False
    return "".join(out)


ROMAN = re.compile(r"^(?=[MDCLXVI])M{0,3}(C[MD]|D?C{0,3})(X[CL]|L?X{0,3})(I[XV]|V?I{0,3})$")
SMALL_WORDS = set("a an and as at but by down for from in into nor of on onto or over per "
                  "so than the to up upon via vs with yet".split())
DEFAULT_ACRONYMS = set("""DNA RNA mRNA USA US UK EU UN NASA ESA CERN LHC AI ML NLP GPU CPU TPU
MRI fMRI EEG ECG HIV AIDS COVID SARS CO2 QCD QED IEEE ACM EPFL ETH MIT PDE PDEs ODE ODEs SDE LSTM CNN
CNNs RNN GAN GANs BERT GPT SQL XML HTML API APIs IoT MOSFET CMOS LED LEDs NMR PCR ATP GDP OECD WHO
ICU UAV UAVs GPS LiDAR SLAM MPC PID FPGA ASIC VLSI RF MIMO OFDM LTE 5G 6G WiFi TCP IP HTTP DFT
MD FEM CFD DNS LES RANS SVM PCA ICA EM MCMC HMM CRF VAE VAEs NeRF RGB RGBD CT PET SPECT UV IR XRD
SEM TEM AFM STM EPR ESR QM MHD LIGO ISO TeX LaTeX BibTeX CRISPR""".split())
PARTICLES = set("van von der den de del della di da dos das du le la ter ten zu y e bin ibn al el".split())


def split_words(s: str) -> list[str]:
    return re.split(r"(\s+)", s)


# Proper nouns common in scientific titles.  STRONG ones are capitalised even
# when found in lower case; KEEP ones (also ordinary English words) are only
# left alone when already capitalised.
PROPER_STRONG = set("""abelian adam aharonov anderson arnoldi bayes bayesian bekenstein berezinskii bessel bethe
bloch bogoliubov bohm bohr boltzmann boolean bose brownian cartesian cauchy chebyshev chern cholesky choi
clifford coulomb crank cummings darwin darwinian debye dicke dirac dirichlet dyson efimov einstein euclidean
euler eulerian feshbach feynman fermi fibonacci floquet fock fokker fourier fredholm galilean gauss gaussian
ginzburg glauber goldstone gram grover haar hadamard haldane hamilton hamiltonian hartree hawking heisenberg
hermite hermitian higgs hilbert hofstadter holevo hopf hubbard huckel hückel ising jacobi jaynes josephson
jozsa kalman keldysh kepler keplerian kirkpatrick kitaev kohn kolmogorov kondo kosterlitz kraus kramers
krylov kullback kuramoto lagrange lagrangian laguerre lanczos landau langevin laplace laplacian legendre
leibler lindblad lindbladian liouville liouvillian lipschitz lorentz lorentzian luttinger lyapunov majorana
markov markovian maxwell maxwellian metropolis minkowski monte carlo mott nambu navier newton newtonian
noether nyquist onsager ornstein pareto parisi pauli peierls pitaevskii planck poisson poissonian potts
rabi raman rayleigh renyi rényi riemann riemannian runge rydberg schmidt schrodinger schrödinger schwarzschild
schwinger shannon sherrington shor slater sobolev sommerfeld suzuki thouless toffoli tomonaga trotter tsallis
uhlenbeck unruh verlet wannier wasserstein weyl wigner wishart wolff yukawa zeeman kutta nicolson
python matlab fortran julia qiskit cirq pennylane tensorflow pytorch numpy scipy mathematica""".split())
PROPER_KEEP = set("""page hall green bell lie wick stark young simon thomas mills wave kerr stokes wilson yang
ye kane mele wang swendsen hastings gibbs edwards anderson berry porter hurwitz jensen nash dyson gross
reynolds kerr wagner mermin""".split())
SPECIAL_SPELLING = {"python": "Python", "matlab": "MATLAB", "fortran": "Fortran", "qiskit": "Qiskit", "cirq": "Cirq",
                    "pennylane": "PennyLane", "tensorflow": "TensorFlow", "pytorch": "PyTorch", "numpy": "NumPy",
                    "scipy": "SciPy", "mathematica": "Mathematica", "julia": "Julia"}
NOT_SENTENCE_END = re.compile(r"(?:\b(?:vs|e\.g|i\.e|cf|al|etc|eq|eqs|fig|figs|ref|refs|no|vol|dr|mr|mrs|st|approx|ca)\.|\b[A-Za-z]\.)$", re.I)


_PROPER_SUFFIXES = ("s", "'s", "’s", "ity", "ities", "ness", "ian", "ians", "ize", "ized", "ization", "ean")


def is_proper(low: str) -> bool:
    """Known proper noun or a derivative (gaussianity, hamiltonians, markovianity)."""
    if low in PROPER_STRONG:
        return True
    return any(low.endswith(suf) and len(low) - len(suf) >= 4 and low[:-len(suf)] in PROPER_STRONG for suf in _PROPER_SUFFIXES)


def _sentence_initial_flags(tokens: list[str]) -> list[bool]:
    """True for words that start a sentence/subtitle (after ':', '?', '!', '.')."""
    flags, start = [], True
    for t in tokens:
        if t.isspace() or not t:
            flags.append(False)
            continue
        flags.append(start)
        u = latex.to_unicode(t)
        start = bool(re.search(r"[:?!.]$", u)) and not NOT_SENTENCE_END.search(u)
    return flags


def _core(part: str):
    m = re.match(r"^([(\[\"'`]*)(.*?)([)\],.:;!?\"']*)$", part, re.S)
    return m.groups()


def looks_sentence_cased(title: str, proper: set[str] | None = None) -> bool:
    """A title where most content words after the first are lower case: its
    capitals are deliberate (proper nouns) and must be kept."""
    proper = proper or set()
    toks = split_words(title)
    initial = _sentence_initial_flags(toks)
    capped = total = 0
    for tok, first in zip(toks, initial):
        if not tok or tok.isspace() or first:
            continue
        for k, part in enumerate(re.split(r"(-|/)", tok)):
            if part in ("-", "/", "") or (k == 0 and first):
                continue
            core = _core(part)[1]
            shape = word_shape(core)
            u = latex.to_unicode(core).lower()
            if shape not in ("cap", "lower") or u in SMALL_WORDS or is_proper(u) or u in PROPER_KEEP or u in proper:
                continue
            total += 1
            capped += shape == "cap"
    return total > 0 and capped / total < 0.6


def to_sentence_case(title: str, *, acronyms: set[str] | None = None,
                     proper_nouns: set[str] | None = None, from_all_caps: bool = False) -> str:
    """Sentence case, preserving LaTeX, acronyms, braces, math and proper nouns.

    * a title that is already in sentence case keeps all its capitals (they are
      deliberate) — only well-known proper nouns written in lower case are
      capitalised (ising → Ising) and the word after a colon gets a capital;
    * in Title Case titles, capitalised words are lower-cased except known
      proper nouns, acronyms (S-K, BCS-BEC), mixed case (iPhone, quTARANG) and
      single capital letters before a hyphen (X-ray, D-Wave)."""
    acr = DEFAULT_ACRONYMS | (acronyms or set())
    acr_l = {a.lower(): a for a in acr}
    proper = {p.lower() for p in (proper_nouns or set())}
    keep_caps = (not from_all_caps) and looks_sentence_cased(title, proper)
    toks = split_words(title)
    initial = _sentence_initial_flags(toks)
    out = []
    for tok, first in zip(toks, initial):
        if not tok or tok.isspace():
            out.append(tok)
            continue
        tu_ = latex.to_unicode(tok)
        letters = [c for c in tu_ if c.isalpha()]
        if not from_all_caps and letters:
            # whole-token protections: acronyms with hyphens (S-K, BCS-BEC), brand/mixed case (i-SPin, quTARANG)
            ups = sum(c.isupper() for c in letters)
            if (ups >= 2 and ups == len(letters)) or (ups >= 1 and any(c.isupper() for c in letters[1:]) and any(c.islower() for c in letters)
                                                        and not re.fullmatch(r"[A-Z][a-z]*(-[A-Z][a-z]*)+[.,:;!?]*", tu_)):
                out.append(tok)
                continue
        parts = re.split(r"(-|/)", tok)
        hyphen_proper = sum(1 for x in parts if x not in ("-", "/", "") and is_proper(latex.to_unicode(_core(x)[1]).lower())) >= 1 and len(parts) > 1
        new_parts = []
        for k, part in enumerate(parts):
            if part in ("-", "/") or not part:
                new_parts.append(part)
                continue
            pre, core, post = _core(part)
            if not core:
                new_parts.append(part)
                continue
            shape = word_shape(core)
            uni = latex.to_unicode(core)
            low = uni.lower()
            is_first = first and k == 0
            nxt_hyphen = k + 1 < len(parts) and parts[k + 1] == "-"
            if shape in ("protected", "other"):
                new = core
            elif low in acr_l:
                new = acr_l[low] if from_all_caps else core
            elif ROMAN.match(uni) and (from_all_caps or shape == "upper") and len(uni) > 1:
                new = core
            elif shape in ("mixed", "upper") and not from_all_caps:
                new = core          # iPhone, McDonald, LaTeX, acronyms ...
            elif uni == "I":
                new = core          # English pronoun
            elif low in SPECIAL_SPELLING and not ("\\" in core or "{" in core):
                new = SPECIAL_SPELLING[low]
            elif is_proper(low) or low in proper or (low in PROPER_KEEP and hyphen_proper):
                new = recase_word(core, "cap")      # Ising, Gaussianity, Gross--Pitaevskii
            elif low in PROPER_KEEP and (shape == "cap" and not from_all_caps):
                new = core
            elif is_first:
                new = recase_word(core, "cap")
            elif shape == "cap" and (keep_caps or (len(uni) == 1 and nxt_hyphen)):
                new = core          # deliberate capital in a sentence-case title / X-ray, D-Wave
            else:
                new = recase_word(core, "lower")
            new_parts.append(pre + new + post)
        out.append("".join(new_parts))
    return "".join(out)


def to_title_case(title: str) -> str:
    toks = split_words(title)
    initial = _sentence_initial_flags(toks)
    words = [i for i, t in enumerate(toks) if t and not t.isspace()]
    last = words[-1] if words else -1
    out = []
    for idx, (tok, first) in enumerate(zip(toks, initial)):
        if not tok or tok.isspace():
            out.append(tok)
            continue
        m = re.match(r"^([(\[\"'`]*)(.*?)([)\],.:;!?\"']*)$", tok, re.S)
        pre, core, post = m.groups()
        shape = word_shape(core)
        uni = latex.to_unicode(core).lower()
        if shape in ("protected", "other", "mixed", "upper"):
            out.append(tok)
        elif uni in SMALL_WORDS and not first and idx != last:
            out.append(pre + recase_word(core, "lower") + post)
        else:
            parts = re.split(r"(-)", core)
            parts = [recase_word(p, "cap") if p and p != "-" and word_shape(p) == "lower" else p for p in parts]
            out.append(pre + "".join(parts) + post)
    return "".join(out)


def is_shouting(s: str) -> bool:
    """True for ALL-CAPS text (at least 2 words and 6 letters)."""
    u = latex.to_unicode(s or "")
    letters = [c for c in u if c.isalpha()]
    if len(letters) < 6 or len(u.split()) < 2:
        return False
    up = sum(1 for c in letters if c.isupper())
    return up / len(letters) > 0.9


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------

def name_proper_case(name: str) -> str:
    """'VAN DER BERG' -> 'van der Berg', 'MCDONALD' -> 'McDonald', 'O'BRIEN' -> "O'Brien"."""
    toks = split_words(name)
    out = []
    nonspace = [t for t in toks if t and not t.isspace()]
    for t in toks:
        if not t or t.isspace():
            out.append(t)
            continue
        u = latex.to_unicode(t)
        if u.lower() in PARTICLES and t is not nonspace[-1] and len(nonspace) > 1:
            out.append(recase_word(t, "lower"))
            continue
        parts = re.split(r"(-|')", t)
        res = []
        for p in parts:
            if p in ("-", "'") or not p:
                res.append(p)
                continue
            w = recase_word(p, "cap")
            pu = latex.to_unicode(w)
            if len(pu) > 3 and pu.startswith("Mc") and w.startswith("Mc"):
                w = "Mc" + recase_word(w[2:], "cap")
            res.append(w)
        out.append("".join(res))
    return "".join(out)


def normalize_initials(first: str) -> str:
    """'J.R.' -> 'J. R.', 'J R' -> 'J. R.', 'J.-P.' kept, full names untouched."""
    if not first or "\\" in first or "$" in first:
        return first
    s = first.strip()
    # pure initials pattern: sequences of single letters optionally with dots
    if re.fullmatch(r"(?:[A-Z]\.?(?:\s*|-))+", s) and len(re.findall(r"[A-Z]", s)) <= 5:
        if re.search(r"[A-Z]{2,}", s.replace(".", "").replace(" ", "")) and "." not in s and " " not in s and "-" not in s:
            return first  # e.g. 'JR' — ambiguous (could be a name like 'JR' / 'Al')
        parts = re.findall(r"[A-Z]\.?(?:-[A-Z]\.?)?", s)
        fixed = []
        for p in parts:
            p = re.sub(r"([A-Z])(?!\.)", r"\1.", p)
            fixed.append(p)
        return " ".join(fixed)
    # mixed: 'John R' -> 'John R.'
    toks = s.split()
    changed = [t + "." if re.fullmatch(r"[A-Z]", t) else t for t in toks]
    return " ".join(changed)


ORG_WORDS = re.compile(r"\b(university|universit|institut|institute|consortium|collaboration|group|team|"
                       r"society|association|committee|council|agency|ministry|department|office|"
                       r"organi[sz]ation|foundation|center|centre|laborator|inc|ltd|gmbh|corp|company|"
                       r"project|network|working|initiative|board|commission|bank|nations|federal|"
                       r"national|international|school|college|press|publishing|editors?|wikipedia)\b", re.I)


def split_single_field_name(name: str) -> tuple[str, str, float] | None:
    """Try to split a single-field creator into (last, first, confidence)."""
    n = collapse_ws(name)
    if not n or ORG_WORDS.search(latex.to_unicode(n)):
        return None
    if n.count(",") == 1:
        last, first = [x.strip() for x in n.split(",")]
        if last and first and len(last.split()) <= 4 and not re.search(r"\d", n):
            if first.lower() in ("jr", "jr.", "sr", "sr.", "ii", "iii"):
                return None
            return last, first, 0.9
        return None
    toks = n.split()
    if 2 <= len(toks) <= 4 and all(re.match(r"^(\{?\\|[^\W\d_])", t) for t in toks):
        # particles belong to the last name
        k = len(toks) - 1
        while k > 1 and latex.to_unicode(toks[k - 1]).lower() in PARTICLES:
            k -= 1
        return " ".join(toks[k:]), " ".join(toks[:k]), 0.55
    return None


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------
DOI_IN_TEXT = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}]+)", re.I)
DOI_STRICT = re.compile(r"^10\.\d{4,9}/\S+$", re.I)
ARXIV_NEW = re.compile(r"(?<![\w.])(\d{4}\.\d{4,5})(v\d+)?(?![\w])")
ARXIV_OLD = re.compile(r"(?<![\w-])((?:astro-ph|cond-mat|gr-qc|hep-ex|hep-lat|hep-ph|hep-th|math-ph|"
                       r"nlin|nucl-ex|nucl-th|physics|quant-ph|math|cs|q-bio|q-fin|stat|eess|econ|"
                       r"chao-dyn|solv-int|patt-sol|adap-org|comp-gas|chem-ph|alg-geom|dg-ga|funct-an|"
                       r"q-alg|mtrl-th|supr-con|acc-phys|ao-sci|atom-ph|bayes-an|plasm-ph)"
                       r"(?:\.[A-Z]{2})?/\d{7})(v\d+)?")


def clean_doi(x: str) -> str:
    x = (x or "").strip()
    x = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", x, flags=re.I)
    x = re.sub(r"^doi:\s*", "", x, flags=re.I)
    x = re.sub(r"^https?://(?:www\.)?[^/]*?/doi/(?:abs/|full/|pdf/|epdf/)?(?=10\.)", "", x, flags=re.I)
    x = x.strip().rstrip(" .;,)]}")
    try:
        from urllib.parse import unquote
        if "%2F" in x or "%2f" in x:
            x = unquote(x)
    except Exception:
        pass
    return x


def find_doi(text: str) -> str:
    if not text:
        return ""
    m = DOI_IN_TEXT.search(text)
    if not m:
        return ""
    d = m.group(1)
    d = re.sub(r"(?:[.,;:)\]}>]|/full|/abstract|/pdf|\.pdf|/epdf)+$", "", d)
    return d


def arxiv_from(text: str) -> str:
    """Extract an arXiv identifier (without version) from text/URL/DOI."""
    if not text:
        return ""
    t = text
    m = re.search(r"10\.48550/arxiv\.(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})", t, re.I)
    if m:
        return m.group(1)
    m = re.search(r"arxiv\.org/(?:abs|pdf|html)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?(?:[?#\s]|$)", t, re.I)
    if m:
        return m.group(1)
    m = re.search(r"arxiv[:\s]*(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})", t, re.I)
    if m:
        return m.group(1)
    return ""


def issn_valid(issn: str) -> bool:
    s = issn.replace("-", "").upper()
    if not re.fullmatch(r"\d{7}[\dX]", s):
        return False
    total = sum(int(c) * w for c, w in zip(s[:7], range(8, 1, -1)))
    check = (11 - total % 11) % 11
    return s[7] == ("X" if check == 10 else str(check))


def normalize_issn_field(v: str) -> str:
    parts = re.findall(r"\b\d{4}-?\d{3}[\dxX]\b", v or "")
    if not parts:
        return v
    out = []
    for p in parts:
        p = p.replace("-", "").upper()
        out.append(p[:4] + "-" + p[4:])
    seen = []
    for p in out:
        if p not in seen:
            seen.append(p)
    return ", ".join(seen)


def isbn_valid(isbn: str) -> bool:
    s = re.sub(r"[\s-]", "", isbn).upper()
    if re.fullmatch(r"\d{9}[\dX]", s):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
        return total % 11 == 0
    if re.fullmatch(r"\d{13}", s):
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s))
        return total % 10 == 0
    return False


def normalize_isbn_field(v: str) -> str:
    s = re.sub(r"(?i)\bISBN(?:-1[03])?:?\s*", "", v or "")
    parts = re.findall(r"[\dXx][\dXx\s-]{8,16}[\dXx]", s)
    if not parts:
        return (v or "").strip()
    out = []
    for p in parts:
        c = re.sub(r"[\s]", "", p).upper()
        if c not in out:
            out.append(c)
    return " ".join(out)


TRACKING = re.compile(r"^(utm_[a-z]+|fbclid|gclid|dclid|mc_[a-z]+|_hs[a-z]+|hsCtaTracking|"
                      r"mkt_tok|yclid|igshid|ref_src|__twitter_impression|trk|originalsearch|"
                      r"casa_token|WT\.[a-z_]+|s_cid|elqTrack|spm|vero_[a-z]+|sc_cid)$", re.I)


def clean_url(u: str, proxy_suffixes: list[str] | None = None) -> str:
    u = (u or "").strip()
    if not u or not re.match(r"https?://", u, re.I):
        return u
    try:
        p = urlsplit(u)
    except ValueError:
        return u
    host = p.hostname or ""
    netloc = p.netloc
    for suf in proxy_suffixes or []:
        suf = suf.strip().lstrip(".").lower()
        if suf and host.lower().endswith("." + suf):
            inner = host[: -(len(suf) + 1)]
            netloc = inner.replace("--", "\0").replace("-", ".").replace("\0", "-")
            break
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    query = urlencode(q, doseq=True) if q != parse_qsl(p.query, keep_blank_values=True) else p.query
    return urlunsplit((p.scheme, netloc, p.path, query, p.fragment))


PAGE_RANGE = re.compile(r"^\s*(?:pp?\.?\s*)?([A-Za-z]{0,3}\d+[A-Za-z]{0,3})\s*(?:-+|–|—|‐|‑|to)\s*([A-Za-z]{0,3}\d+[A-Za-z]{0,3})\s*\.?\s*$", re.I)


def normalize_pages(p: str, sep: str = "--") -> str:
    if not p:
        return p
    m = PAGE_RANGE.match(p)
    if m:
        a, b = m.groups()
        if a == b:
            return a
        return f"{a}{sep}{b}"
    m = re.match(r"^\s*(?:pp?\.?\s*)(\S+)\s*$", p, re.I)
    if m:
        return m.group(1)
    return p.strip()


LANG_MAP = {
    "en": ["english", "eng", "en", "en-us", "en_us", "en-gb", "en_gb", "englisch", "anglais", "inglese", "inglés", "en-ca", "en-au"],
    "de": ["german", "deutsch", "ger", "deu", "de", "de-de", "de_de", "de-ch", "de-at", "allemand", "tedesco"],
    "fr": ["french", "français", "francais", "fre", "fra", "fr", "fr-fr", "fr_fr", "fr-ch", "französisch", "francese"],
    "it": ["italian", "italiano", "ita", "it", "it-it", "it_it", "it-ch", "italienisch", "italien"],
    "es": ["spanish", "español", "espanol", "spa", "es", "es-es", "castellano"],
    "pt": ["portuguese", "português", "portugues", "por", "pt", "pt-br", "pt-pt"],
    "nl": ["dutch", "nederlands", "dut", "nld", "nl", "nl-nl"],
    "ru": ["russian", "русский", "rus", "ru"],
    "zh": ["chinese", "中文", "chi", "zho", "zh", "zh-cn", "zh-tw"],
    "ja": ["japanese", "日本語", "jpn", "ja"],
    "pl": ["polish", "polski", "pol", "pl"],
    "sv": ["swedish", "svenska", "swe", "sv"],
}
LANG_LOOKUP = {v: k for k, vs in LANG_MAP.items() for v in vs}


def normalize_language(v: str, keep_region: bool = False) -> str:
    s = (v or "").strip()
    if not s:
        return s
    key = s.lower()
    if key in LANG_LOOKUP:
        code = LANG_LOOKUP[key]
        if keep_region and re.fullmatch(r"[a-z]{2}[-_][A-Za-z]{2}", s):
            return s[:2].lower() + "-" + s[3:].upper()
        return code
    return s
