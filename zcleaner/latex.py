"""Unicode <-> LaTeX conversion for bibliographic metadata.

Design goals
------------
* ``to_latex`` never breaks LaTeX that is already present: commands, math
  (``$...$``), escaped characters and brace groups are copied verbatim and only
  *raw* Unicode characters / unescaped specials in text mode are converted.
* ``to_unicode`` renders LaTeX back to Unicode.  It is used for comparisons
  (so ``Schr{\\"o}dinger`` == ``Schrödinger``), for previews in the GUI and for
  building file names.
* Output style follows BibTeX conventions: accented letters are wrapped in a
  brace group that starts with a backslash (``{\\"o}``), which keeps them intact
  when BibTeX changes case and sorts.
"""
from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

# combining mark -> accent command
ACCENTS = {
    "\u0300": "`", "\u0301": "'", "\u0302": "^", "\u0303": "~", "\u0304": "=",
    "\u0306": "u", "\u0307": ".", "\u0308": '"', "\u030a": "r", "\u030b": "H",
    "\u030c": "v", "\u0327": "c", "\u0328": "k", "\u0323": "d", "\u0331": "b",
    "\u0361": "t",
}
ACCENT_TO_MARK = {v: k for k, v in ACCENTS.items()}
SYMBOL_ACCENTS = set("`'^~=.\"")
LETTER_ACCENTS = set("uvHckdbrt")
BELOW_ACCENTS = {"c", "k", "d", "b"}  # never use dotless i under these

SPECIAL_LETTERS = {
    "ß": "ss", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "å": "aa", "Å": "AA", "ł": "l", "Ł": "L", "ı": "i", "ȷ": "j", "ð": "dh",
    "Ð": "DH", "þ": "th", "Þ": "TH", "đ": "dj", "Đ": "DJ", "ŋ": "ng", "Ŋ": "NG",
}
SPECIAL_REVERSE = {v: k for k, v in SPECIAL_LETTERS.items()}

# text-mode symbols; value is inserted verbatim
TEXT_SYMBOLS = {
    "\u2013": "--", "\u2014": "---", "\u2012": "--", "\u2010": "-", "\u2011": "-",
    "\u2018": "`", "\u2019": "'", "\u201c": "``", "\u201d": "''", "\u201e": ",,",
    "\u201a": ",", "\u2026": "\\ldots{}", "\u00a0": "~", "\u2009": "\\,",
    "\u202f": "\\,", "\u2002": " ", "\u2003": " ", "\u2007": " ", "\u2008": " ",
    "\u200a": " ", "\u205f": " ", "\u3000": " ", "\u200b": "", "\u200c": "",
    "\u200d": "", "\ufeff": "", "\u00ad": "",
    "§": "{\\S}", "¶": "{\\P}", "©": "{\\copyright}", "®": "{\\textregistered}",
    "™": "{\\texttrademark}", "°": "{\\textdegree}", "†": "{\\dag}",
    "‡": "{\\ddag}", "£": "{\\pounds}", "€": "{\\texteuro}", "¿": "{?`}",
    "¡": "{!`}", "«": "{\\guillemotleft}", "»": "{\\guillemotright}",
    "‹": "{\\guilsinglleft}", "›": "{\\guilsinglright}", "•": "{\\textbullet}",
    "‰": "{\\textperthousand}", "¢": "{\\textcent}", "¥": "{\\textyen}",
    "ª": "{\\textordfeminine}", "º": "{\\textordmasculine}",
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}

# math-mode symbols (value without $)
MATH = {
    # greek lower
    "α": "\\alpha", "β": "\\beta", "γ": "\\gamma", "δ": "\\delta", "ε": "\\varepsilon",
    "ϵ": "\\epsilon", "ζ": "\\zeta", "η": "\\eta", "θ": "\\theta", "ϑ": "\\vartheta",
    "ι": "\\iota", "κ": "\\kappa", "λ": "\\lambda", "μ": "\\mu", "µ": "\\mu",
    "ν": "\\nu", "ξ": "\\xi", "ο": "o", "π": "\\pi", "ϖ": "\\varpi", "ρ": "\\rho",
    "ϱ": "\\varrho", "σ": "\\sigma", "ς": "\\varsigma", "τ": "\\tau",
    "υ": "\\upsilon", "φ": "\\varphi", "ϕ": "\\phi", "χ": "\\chi", "ψ": "\\psi",
    "ω": "\\omega",
    # greek upper (only those that differ from latin)
    "Γ": "\\Gamma", "Δ": "\\Delta", "Θ": "\\Theta", "Λ": "\\Lambda", "Ξ": "\\Xi",
    "Π": "\\Pi", "Σ": "\\Sigma", "Υ": "\\Upsilon", "Φ": "\\Phi", "Ψ": "\\Psi",
    "Ω": "\\Omega", "Ω": "\\Omega",
    # operators / relations
    "±": "\\pm", "∓": "\\mp", "×": "\\times", "÷": "\\div", "·": "\\cdot",
    "⋅": "\\cdot", "∗": "\\ast", "−": "-", "≤": "\\leq", "≥": "\\geq",
    "≦": "\\leqq", "≧": "\\geqq", "≪": "\\ll", "≫": "\\gg", "≠": "\\neq",
    "≈": "\\approx", "∼": "\\sim", "≃": "\\simeq", "≅": "\\cong", "≡": "\\equiv",
    "∝": "\\propto", "∞": "\\infty", "∂": "\\partial", "∇": "\\nabla",
    "∑": "\\sum", "∏": "\\prod", "∫": "\\int", "∮": "\\oint", "√": "\\surd",
    "∈": "\\in", "∉": "\\notin", "∋": "\\ni", "⊂": "\\subset", "⊃": "\\supset",
    "⊆": "\\subseteq", "⊇": "\\supseteq", "∪": "\\cup", "∩": "\\cap",
    "∅": "\\emptyset", "∀": "\\forall", "∃": "\\exists", "¬": "\\neg",
    "∧": "\\wedge", "∨": "\\vee", "⊕": "\\oplus", "⊗": "\\otimes", "⊙": "\\odot",
    "∘": "\\circ", "†": "\\dagger", "⊥": "\\perp", "∥": "\\parallel", "‖": "\\|",
    "⟨": "\\langle", "⟩": "\\rangle", "〈": "\\langle", "〉": "\\rangle",
    "→": "\\rightarrow", "←": "\\leftarrow", "↔": "\\leftrightarrow",
    "⇒": "\\Rightarrow", "⇐": "\\Leftarrow", "⇔": "\\Leftrightarrow",
    "↦": "\\mapsto", "↑": "\\uparrow", "↓": "\\downarrow", "⟶": "\\longrightarrow",
    "ℓ": "\\ell", "ℏ": "\\hbar", "ℜ": "\\Re", "ℑ": "\\Im", "ℵ": "\\aleph",
    "℘": "\\wp", "ℝ": "\\mathbb{R}", "ℕ": "\\mathbb{N}", "ℤ": "\\mathbb{Z}",
    "ℚ": "\\mathbb{Q}", "ℂ": "\\mathbb{C}", "ℋ": "\\mathcal{H}", "ℒ": "\\mathcal{L}",
    "′": "\\prime", "″": "\\prime\\prime", "∠": "\\angle", "△": "\\triangle",
    "□": "\\square", "⋯": "\\cdots", "⋮": "\\vdots", "⋱": "\\ddots",
    "∖": "\\setminus", "≔": "\\coloneqq", "≺": "\\prec", "≻": "\\succ",
    "⊢": "\\vdash", "⊨": "\\models", "∣": "\\mid", "⌊": "\\lfloor",
    "⌋": "\\rfloor", "⌈": "\\lceil", "⌉": "\\rceil", "√": "\\surd",
}
# the "†" key is ambiguous (text \dag vs math \dagger); the text one wins
MATH.pop("†", None)
MATH_REVERSE: dict[str, str] = {}
for _k, _v in MATH.items():
    MATH_REVERSE.setdefault(_v, _k)
MATH_REVERSE.update({"\\le": "≤", "\\ge": "≥", "\\ne": "≠", "\\to": "→",
                     "\\epsilon": "ϵ", "\\varepsilon": "ε", "\\phi": "ϕ",
                     "\\varphi": "φ", "\\mu": "μ", "\\dagger": "†",
                     "\\gets": "←", "\\lnot": "¬", "\\land": "∧", "\\lor": "∨",
                     "\\sqrt": "√", "\\surd": "√", "\\ldots": "…", "\\dots": "…"})

SUPERSCRIPTS = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
                "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-",
                "⁼": "=", "⁽": "(", "⁾": ")", "ⁿ": "n", "ⁱ": "i"}
SUBSCRIPTS = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5",
              "₆": "6", "₇": "7", "₈": "8", "₉": "9", "₊": "+", "₋": "-",
              "₌": "=", "₍": "(", "₎": ")", "ₐ": "a", "ₑ": "e", "ₒ": "o",
              "ₓ": "x", "ₙ": "n", "ₘ": "m", "ₖ": "k", "ₗ": "l", "ₚ": "p",
              "ₛ": "s", "ₜ": "t", "ₕ": "h", "ᵢ": "i", "ⱼ": "j"}
SUP_REVERSE = {v: k for k, v in SUPERSCRIPTS.items()}
SUB_REVERSE = {v: k for k, v in SUBSCRIPTS.items()}

TEXT_COMMANDS = {
    "ldots": "…", "dots": "…", "textellipsis": "…", "S": "§", "P": "¶",
    "copyright": "©", "textcopyright": "©", "textregistered": "®",
    "texttrademark": "™", "textdegree": "°", "dag": "†", "ddag": "‡",
    "pounds": "£", "texteuro": "€", "euro": "€", "guillemotleft": "«",
    "guillemotright": "»", "guillemetleft": "«", "guillemetright": "»",
    "guilsinglleft": "‹", "guilsinglright": "›", "textbullet": "•",
    "textperthousand": "‰", "textcent": "¢", "textyen": "¥",
    "textendash": "–", "textemdash": "—", "textordfeminine": "ª",
    "textordmasculine": "º", "textquestiondown": "¿", "textexclamdown": "¡",
    "textquoteleft": "‘", "textquoteright": "’", "textquotedblleft": "“",
    "textquotedblright": "”", "textasciitilde": "~", "textbackslash": "\\",
    "textunderscore": "_", "textasciicircum": "^", "textbar": "|",
    "textless": "<", "textgreater": ">", "LaTeX": "LaTeX", "TeX": "TeX",
    "LaTeXe": "LaTeX2e", "BibTeX": "BibTeX", "slash": "/", "textslash": "/",
    "textmu": "µ", "textalpha": "α", "textbeta": "β",
}
# commands whose single argument is kept (formatting only)
ARG_COMMANDS = {
    "textit", "emph", "textbf", "textsc", "textrm", "textsf", "texttt", "textup",
    "textsl", "textmd", "textnormal", "mbox", "hbox", "text", "mathrm", "mathit",
    "mathbf", "mathsf", "mathtt", "mathnormal", "operatorname", "NoCaseChange",
    "nocase", "url", "uppercase", "MakeUppercase", "lowercase", "MakeLowercase",
    "underline", "textsubscript", "textsuperscript", "ensuremath", "boldsymbol",
    "bm", "mathscr", "mathcal", "mathbb", "mathfrak", "vec", "hat", "bar",
    "tilde", "dot", "ddot", "overline", "widehat", "widetilde", "textnormal",
}
# commands that are silently dropped with their (optional) arguments
DROP_COMMANDS = {"relax", "protect", "nobreak", "noopsort", "hspace", "vspace",
                 "label", "cite", "footnote", "index", "linebreak", "newline",
                 "allowbreak", "sortnoop", "noop"}
SPACE_COMMANDS = {",": " ", ";": " ", ":": " ", " ": " ", "!": "", "-": "",
                  "/": "", "@": "", "quad": " ", "qquad": " ", "enspace": " ",
                  "thinspace": " ", "newline": " ", "\\": " "}
ESCAPABLE = set("&%#_$\\{}")

# --------------------------------------------------------------------------
# Unicode -> LaTeX
# --------------------------------------------------------------------------


def _accent_expr(base: str, marks: list[str], style: str) -> str | None:
    """Build LaTeX for base + combining marks, or None if unsupported."""
    if base in SPECIAL_LETTERS:
        expr = "\\" + SPECIAL_LETTERS[base]
    elif base.isascii() and base.isalpha():
        expr = base
    else:
        return None
    cmds = []
    for m in marks:
        if m not in ACCENTS:
            return None
        cmds.append(ACCENTS[m])
    if not cmds:
        return None
    # dotless i/j under an above-accent
    if expr in ("i", "j") and cmds[0] not in BELOW_ACCENTS:
        expr = "\\" + expr
    if len(cmds) == 1 and style == "braced":
        c = cmds[0]
        if c in SYMBOL_ACCENTS:
            return "{\\%s%s}" % (c, expr)
        return "{\\%s %s}" % (c, expr) if len(expr) == 1 else "{\\%s{%s}}" % (c, expr)
    for c in cmds:
        expr = "\\%s{%s}" % (c, expr)
    return "{%s}" % expr if style == "braced" else expr


def char_to_latex(ch: str, style: str = "braced") -> tuple[str, str] | None:
    """Return (mode, latex) where mode is 'text' or 'math', or None."""
    if ch in TEXT_SYMBOLS:
        return "text", TEXT_SYMBOLS[ch]
    if ch in SPECIAL_LETTERS:
        return "text", "{\\%s}" % SPECIAL_LETTERS[ch]
    if ch in MATH:
        return "math", MATH[ch]
    if ch in SUPERSCRIPTS:
        return "sup", SUPERSCRIPTS[ch]
    if ch in SUBSCRIPTS:
        return "sub", SUBSCRIPTS[ch]
    d = unicodedata.normalize("NFD", ch)
    if len(d) > 1:
        e = _accent_expr(d[0], list(d[1:]), style)
        if e:
            return "text", e
    k = unicodedata.normalize("NFKC", ch)
    if k != ch and k.isascii() and k.strip():
        return "text", k
    return None


def _join_math(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if out and re.search(r"\\[A-Za-z]+$", out) and re.match(r"[A-Za-z0-9]", p):
            out += " "
        out += p
    return out


def _is_greek(ch: str) -> bool:
    return "GREEK" in unicodedata.name(ch, "") and ch.isalpha() and ch != "µ"


def _greek_text_positions(s: str) -> set[int]:
    """Positions of characters that belong to Greek *words* (natural-language
    Greek text, not isolated math symbols).  A run of >=3 Greek letters, or any
    run containing accented Greek letters, is treated as text and left as is."""
    pos: set[int] = set()
    i, n = 0, len(s)
    while i < n:
        if _is_greek(s[i]):
            j = i
            while j < n and _is_greek(s[j]):
                j += 1
            run = s[i:j]
            if len(run) >= 3 or any(c not in MATH for c in run):
                pos.update(range(i, j))
            i = j
        else:
            i += 1
    return pos


def to_latex(s: str, *, style: str = "braced", escape_specials: bool = True) -> str:
    """Convert raw Unicode characters in *s* to LaTeX, leaving LaTeX intact."""
    if not s:
        return s
    s = unicodedata.normalize("NFC", s)
    greek_text = _greek_text_positions(s)
    out: list[str] = []
    math_buf: list[str] = []   # pending math content (outside of existing $..$)
    in_math = False
    i, n = 0, len(s)

    def flush_math():
        if math_buf:
            out.append("$" + _join_math(math_buf) + "$")
            math_buf.clear()

    while i < n:
        ch = s[i]
        # ----- existing LaTeX: commands / escapes are copied verbatim
        if ch == "\\":
            flush_math()
            m = re.match(r"\\([A-Za-z]+|.)", s[i:])
            tok = m.group(0) if m else "\\"
            out.append(tok)
            i += len(tok)
            continue
        if ch == "$":
            flush_math()
            in_math = not in_math
            out.append("$")
            i += 1
            continue
        if in_math:
            if ch.isascii():
                out.append(ch)
            else:
                r = char_to_latex(ch, style)
                if r and r[0] == "math":
                    prev = out[-1] if out else ""
                    tok = r[1]
                    if re.search(r"\\[A-Za-z]+$", prev) and re.match(r"[A-Za-z]", tok):
                        tok = " " + tok
                    out.append(tok + (" " if tok.startswith("\\") and i + 1 < n and s[i + 1].isalpha() else ""))
                elif r and r[0] in ("sup", "sub"):
                    run, j = [], i
                    table = SUPERSCRIPTS if r[0] == "sup" else SUBSCRIPTS
                    while j < n and s[j] in table:
                        run.append(table[s[j]])
                        j += 1
                    out.append(("^" if r[0] == "sup" else "_") + "{" + "".join(run) + "}")
                    i = j
                    continue
                elif r:
                    out.append(r[1])
                else:
                    out.append(ch)
            i += 1
            continue
        # ----- text mode
        if ch.isascii():
            flush_math()
            if escape_specials and ch in "&%#":
                out.append("\\" + ch)
            elif escape_specials and ch == "_":
                out.append("\\_")
            else:
                out.append(ch)
            i += 1
            continue
        r = None if i in greek_text else char_to_latex(ch, style)
        if r is None:
            flush_math()
            out.append(ch)
            i += 1
            continue
        mode, tex = r
        if mode == "math":
            math_buf.append(tex)
            i += 1
            continue
        if mode in ("sup", "sub"):
            table = SUPERSCRIPTS if mode == "sup" else SUBSCRIPTS
            run, j = [], i
            while j < n and s[j] in table:
                run.append(table[s[j]])
                j += 1
            math_buf.append(("^" if mode == "sup" else "_") + "{" + "".join(run) + "}")
            i = j
            continue
        flush_math()
        out.append(tex)
        i += 1
    flush_math()
    res = "".join(out)
    # a text symbol like \ldots{} followed by nothing: tidy the trailing {}
    res = re.sub(r"\\ldots\{\}$", r"\\ldots", res)
    return res


def unmapped_chars(s: str) -> list[str]:
    """Non-ASCII characters (outside math) that have no LaTeX equivalent."""
    res = []
    for ch in set(s or ""):
        if not ch.isascii() and char_to_latex(ch) is None and unicodedata.category(ch)[0] in "LSP":
            res.append(ch)
    return sorted(res)


# --------------------------------------------------------------------------
# LaTeX -> Unicode
# --------------------------------------------------------------------------

class _Parser:
    def __init__(self, s: str):
        self.s = s
        self.i = 0

    def peek(self, k=0):
        j = self.i + k
        return self.s[j] if j < len(self.s) else ""

    def group(self) -> str:
        """Read a {...} group (assumes current char is '{'); returns raw inner."""
        depth, start = 0, self.i
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    self.i += 1
                    return self.s[start + 1:self.i - 1]
            self.i += 1
        return self.s[start + 1:]

    def arg(self) -> str:
        """Read one macro argument (group or single token), raw."""
        while self.peek() == " ":
            self.i += 1
        c = self.peek()
        if c == "{":
            return self.group()
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", self.s[self.i:])
            tok = m.group(0)
            self.i += len(tok)
            return tok
        self.i += 1
        return c

    def skip_empty_group(self):
        if self.s.startswith("{}", self.i):
            self.i += 2


def _apply_accent(cmd: str, base_raw: str) -> str:
    base = to_unicode(base_raw)
    if base_raw.strip() in ("\\i", "{\\i}"):
        base = "i"
    elif base_raw.strip() in ("\\j", "{\\j}"):
        base = "j"
    mark = ACCENT_TO_MARK.get(cmd)
    if not base:
        return {"`": "`", "'": "´", "^": "^", "~": "~", '"': "¨"}.get(cmd, "")
    if not mark:
        return base
    return unicodedata.normalize("NFC", base[0] + mark + base[1:])


def _decode_math(s: str) -> str:
    p = _Parser(s)
    out = []
    while p.i < len(s):
        c = s[p.i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", s[p.i:])
            tok = m.group(0)
            p.i += len(tok)
            name = m.group(1)
            if tok in MATH_REVERSE and not tok.startswith("\\mathbb") and not tok.startswith("\\mathcal"):
                out.append(MATH_REVERSE[tok])
            elif name in ("mathbb", "mathcal"):
                a = p.arg()
                out.append(MATH_REVERSE.get("\\%s{%s}" % (name, a), _decode_math(a)))
            elif name in ARG_COMMANDS or name in ("frac", "sqrt"):
                if name == "sqrt":
                    out.append("√")
                a = _decode_math(p.arg())
                if name == "frac":
                    b = _decode_math(p.arg())
                    a = f"{a}/{b}"
                out.append(a)
            elif name in SPECIAL_REVERSE:
                out.append(SPECIAL_REVERSE[name])
            elif name in SPACE_COMMANDS:
                pass
            elif len(name) == 1 and not name.isalpha():
                out.append(name)
            elif name in ("left", "right", "big", "Big", "bigl", "bigr", "Bigl", "Bigr", "displaystyle", "limits"):
                pass
            else:
                out.append(name)
        elif c in "^_":
            p.i += 1
            a = _decode_math(p.arg())
            table = SUP_REVERSE if c == "^" else SUB_REVERSE
            if a and all(ch in table for ch in a):
                out.append("".join(table[ch] for ch in a))
            elif a in ("\\prime", "′", "′′"):
                out.append(a)
            else:
                out.append(a)
        elif c in "{}":
            p.i += 1
        elif c.isspace():
            p.i += 1
        else:
            out.append(c)
            p.i += 1
    return "".join(out)


def to_unicode(s: str) -> str:
    """Render LaTeX markup in *s* as plain Unicode text."""
    if not s or ("\\" not in s and "{" not in s and "$" not in s and "--" not in s
                 and "``" not in s and "''" not in s and "~" not in s):
        return s or ""
    p = _Parser(s)
    out: list[str] = []
    n = len(s)
    while p.i < n:
        c = s[p.i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", s[p.i:])
            if not m:
                p.i += 1
                continue
            name = m.group(1)
            p.i += len(m.group(0))
            if name in ACCENT_TO_MARK and (name in SYMBOL_ACCENTS or name in LETTER_ACCENTS):
                if name in LETTER_ACCENTS and p.peek().isalpha() and not p.peek() == " ":
                    # e.g. \vc is not valid; treat as unknown command letters
                    pass
                out.append(_apply_accent(name, p.arg()))
                continue
            if name in SPECIAL_REVERSE:
                out.append(SPECIAL_REVERSE[name])
                if p.peek() == " ":
                    p.i += 1
                p.skip_empty_group()
                continue
            if name in TEXT_COMMANDS:
                out.append(TEXT_COMMANDS[name])
                if p.peek() == " " and name.isalpha():
                    p.i += 1
                p.skip_empty_group()
                continue
            if name in SPACE_COMMANDS:
                out.append(SPACE_COMMANDS[name])
                continue
            if len(name) == 1 and name in ESCAPABLE:
                out.append(name)
                continue
            if name in ARG_COMMANDS:
                a = p.arg()
                if name in ("textsuperscript",):
                    a2 = to_unicode(a)
                    out.append("".join(SUP_REVERSE.get(ch, ch) for ch in a2) if all(ch in SUP_REVERSE for ch in a2) else a2)
                elif name in ("textsubscript",):
                    a2 = to_unicode(a)
                    out.append("".join(SUB_REVERSE.get(ch, ch) for ch in a2) if all(ch in SUB_REVERSE for ch in a2) else a2)
                elif name in ("ensuremath",):
                    out.append(_decode_math(a))
                else:
                    out.append(to_unicode(a))
                continue
            if name in DROP_COMMANDS:
                if p.peek() == "{":
                    p.group()
                continue
            if name in ("href",):
                p.arg()
                out.append(to_unicode(p.arg()))
                continue
            key = "\\" + name
            if key in MATH_REVERSE:  # math command used in text mode
                out.append(MATH_REVERSE[key])
                if p.peek() == " ":
                    p.i += 1
                continue
            # unknown command: keep its name as text
            out.append(name)
            continue
        if c == "$":
            j = p.i + 1
            dbl = s.startswith("$$", p.i)
            if dbl:
                j += 1
            end = s.find("$$" if dbl else "$", j)
            while end != -1 and s[end - 1] == "\\":
                end = s.find("$$" if dbl else "$", end + 1)
            if end == -1:
                out.append("$")
                p.i += 1
                continue
            out.append(_decode_math(s[j:end]))
            p.i = end + (2 if dbl else 1)
            continue
        if c in "{}":
            p.i += 1
            continue
        if c == "~":
            out.append("\u00a0")
            p.i += 1
            continue
        if s.startswith("---", p.i):
            out.append("—")
            p.i += 3
            continue
        if s.startswith("--", p.i):
            out.append("–")
            p.i += 2
            continue
        if s.startswith("``", p.i):
            out.append("“")
            p.i += 2
            continue
        if s.startswith("''", p.i):
            out.append("”")
            p.i += 2
            continue
        if s.startswith("?`", p.i):
            out.append("¿")
            p.i += 2
            continue
        if s.startswith("!`", p.i):
            out.append("¡")
            p.i += 2
            continue
        out.append(c)
        p.i += 1
    return unicodedata.normalize("NFC", "".join(out))


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def check(s: str) -> list[str]:
    """Return a list of LaTeX problems in *s* (empty if fine)."""
    probs: list[str] = []
    if not s:
        return probs
    depth, in_math, i, n = 0, False, 0, len(s)
    bare = set()
    while i < n:
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.?)", s[i:])
            i += max(1, len(m.group(0)))
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                probs.append("closing brace without opening brace")
                depth = 0
        elif c == "$":
            in_math = not in_math
        elif not in_math and c in "&%#":
            bare.add(c)
        elif not in_math and c in "^" :
            bare.add(c)
        i += 1
    if depth > 0:
        probs.append("unbalanced braces (%d unclosed)" % depth)
    if in_math:
        probs.append("unbalanced $ (math mode not closed)")
    for c in sorted(bare):
        probs.append(f"unescaped '{c}' in text mode")
    return probs
