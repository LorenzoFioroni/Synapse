"""Conversion between LaTeX markup (as found in .bib files) and Unicode."""

from __future__ import annotations

import re
import unicodedata

ACCENTS = {
    "'": "\u0301",
    "`": "\u0300",
    "^": "\u0302",
    '"': "\u0308",
    "~": "\u0303",
    "=": "\u0304",
    ".": "\u0307",
    "u": "\u0306",
    "v": "\u030c",
    "H": "\u030b",
    "c": "\u0327",
    "k": "\u0328",
    "r": "\u030a",
    "d": "\u0323",
    "b": "\u0331",
}
SYMBOLS = {
    "ss": "ß",
    "o": "ø",
    "O": "Ø",
    "l": "ł",
    "L": "Ł",
    "ae": "æ",
    "AE": "Æ",
    "oe": "œ",
    "OE": "Œ",
    "aa": "å",
    "AA": "Å",
    "i": "ı",
    "j": "ȷ",
    "textendash": "–",
    "textemdash": "—",
    "textquoteright": "’",
    "textquoteleft": "‘",
    "textquotedblleft": "“",
    "textquotedblright": "”",
    "dag": "†",
    "S": "§",
    "P": "¶",
    "copyright": "©",
    "textregistered": "®",
    "texttrademark": "™",
    "ldots": "…",
    "dots": "…",
    "textasciitilde": "~",
    "textbackslash": "\\",
    "textunderscore": "_",
}
ESCAPES = {"&": "&", "%": "%", "$": "$", "#": "#", "_": "_", "{": "{", "}": "}", " ": " ", ",": "\u2009"}

_ACCENT_RE = re.compile(r"""\\([`'^"~=.])\s*(?:\{\s*(\\?[A-Za-z])\s*\}|(\\?[A-Za-z]))""")
# letter commands (\v, \c, ...) need braces or a space before the base letter
_ACCENT_LETTER_RE = re.compile(r"""\\([uvHckrdb])(?:\s*\{\s*(\\?[A-Za-z])\s*\}|\s+(\\?[A-Za-z]))""")
_SYMBOL_RE = re.compile(r"\\(" + "|".join(sorted(SYMBOLS, key=len, reverse=True)) + r")(?![A-Za-z])\s?(?:\{\})?")
_TEXTCMD_START = re.compile(
    r"\\(?:emph|textit|textbf|textsc|textrm|textsf|texttt|mbox|textup|textnormal|url|NoCaseChange)\s*\{"
)


def _strip_textcmds(s: str) -> str:
    """Unwrap \\textit{...} etc. (brace-balanced, so nested {{...}} works)."""
    while True:
        m = _TEXTCMD_START.search(s)
        if not m:
            return s
        depth, i = 1, m.end()
        while i < len(s) and depth:
            if s[i] == "\\":
                i += 1
            elif s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
            i += 1
        end = i if depth == 0 else len(s)
        inner = s[m.end() : end - 1] if depth == 0 else s[m.end() :]
        s = s[: m.start()] + inner + s[end:]


def _accent(m: re.Match) -> str:
    cmd, base = m.group(1), m.group(2) or m.group(3)
    if base in ("\\i", "\\j"):
        base = base[1]
    return unicodedata.normalize("NFC", base + ACCENTS[cmd])


def _convert_text(s: str) -> str:
    s = _strip_textcmds(s)
    s = _ACCENT_RE.sub(_accent, s)
    s = _ACCENT_LETTER_RE.sub(_accent, s)
    s = _SYMBOL_RE.sub(lambda m: SYMBOLS[m.group(1)], s)
    s = re.sub(r"\\([&%$#_{} ,])", lambda m: ESCAPES[m.group(1)], s)
    s = s.replace("---", "—").replace("--", "–").replace("``", "“").replace("''", "”")
    s = re.sub(r"(?<!\\)~", "\u00a0", s)
    s = re.sub(r"(?<!\\)[{}]", "", s)  # brace protection
    s = s.replace("\u00a0", " ")
    return s


def latex_to_unicode(s: str | None) -> str:
    """Convert LaTeX text to Unicode. Math ($...$) is kept verbatim."""
    if not s:
        return ""
    parts = re.split(r"(?<!\\)(\$[^$]*\$)", s)
    out = [p if p.startswith("$") and p.endswith("$") and len(p) > 1 else _convert_text(p) for p in parts]
    return " ".join("".join(out).split())


_REV_ACCENTS = {v: k for k, v in ACCENTS.items()}
_REV_SYMBOLS = {
    "ß": r"{\ss}",
    "ø": r"{\o}",
    "Ø": r"{\O}",
    "ł": r"{\l}",
    "Ł": r"{\L}",
    "æ": r"{\ae}",
    "Æ": r"{\AE}",
    "œ": r"{\oe}",
    "Œ": r"{\OE}",
    "å": r"{\aa}",
    "Å": r"{\AA}",
    "ı": r"{\i}",
    "–": "--",
    "—": "---",
    "“": "``",
    "”": "''",
    "‘": "`",
    "’": "'",
    "…": r"\ldots{}",
    "§": r"\S{}",
    "©": r"\copyright{}",
}


def escape(s: str | None, unicode: bool = True) -> str:
    """Escape a value for a .bib file. With ``unicode=False`` non-ASCII becomes LaTeX."""
    if not s:
        return ""
    parts = re.split(r"(\$[^$]*\$)", str(s))
    out = []
    for p in parts:
        if p.startswith("$") and p.endswith("$") and len(p) > 1:
            out.append(p)
            continue
        p = re.sub(r"([&%#_])", r"\\\1", p)
        p = p.replace("$", r"\$")
        # unbalanced braces would break the file
        if p.count("{") != p.count("}"):
            p = p.replace("{", r"\{").replace("}", r"\}")
        if not unicode:
            p = _to_latex(p)
        out.append(p)
    return "".join(out)


def _to_latex(s: str) -> str:
    res = []
    for ch in s:
        if ord(ch) < 128:
            res.append(ch)
            continue
        if ch in _REV_SYMBOLS:
            res.append(_REV_SYMBOLS[ch])
            continue
        d = unicodedata.normalize("NFD", ch)
        base, marks = d[0], d[1:]
        if marks and all(m in _REV_ACCENTS for m in marks) and ord(base) < 128:
            r = base if base not in "ij" else "\\" + base
            for m in marks:
                cmd = _REV_ACCENTS[m]
                r = f"\\{cmd}{{{r}}}"
            res.append("{" + r + "}")
        else:
            res.append(ch)
    return "".join(res)


def protect_case(title: str) -> str:
    """Brace words whose capitalisation must survive BibTeX styles (DNA, iPhone, McDonald)."""

    def wrap(m: re.Match) -> str:
        w = m.group(0)
        inner = w[1:]
        if any(c.isupper() for c in inner) and any(c.isalpha() for c in w):
            return "{" + w + "}"
        return w

    out = []
    for part in re.split(r"(\$[^$]*\$|\{[^{}]*\})", title or ""):
        if part.startswith(("$", "{")):
            out.append(part)
        else:
            out.append(re.sub(r"[^\s\-/:,;.()]+", wrap, part))
    return "".join(out)
