"""Citation key generation from a user-configurable pattern.

Tokens (case of the token name selects the case of the output):

    {auth}  {Auth}  {AUTH}      first author's family name
    {authEtAl} {AuthEtAl}       1 author: name, 2: nameName, 3+: nameEtAl
    {authors2} {Authors2}       first two family names joined
    {year}  {yy}                four- / two-digit year
    {title} {Title} {TITLE}     first significant word of the title
    {shorttitle} {ShortTitle}   first three significant words, concatenated
    {journal} {Journal}         initials of the publication ("PRL")
    {doi}                       DOI suffix, alphanumerics only

Anything outside braces is copied literally (e.g. "{auth}_{year}").
"""

from __future__ import annotations

import re
import unicodedata

STOPWORDS = {
    "a",
    "an",
    "the",
    "of",
    "on",
    "in",
    "at",
    "to",
    "for",
    "and",
    "or",
    "with",
    "from",
    "by",
    "via",
    "into",
    "onto",
    "about",
    "over",
    "under",
    "is",
    "are",
    "as",
    "its",
    "their",
    "towards",
    "toward",
    "between",
    "using",
    "new",
}

PRESETS = [
    {"label": "authTitleYear  (cuiVariational2015)", "pattern": "{auth}{Title}{year}"},
    {"label": "authYear  (cui2015)", "pattern": "{auth}{year}"},
    {"label": "AuthYear  (Cui2015)", "pattern": "{Auth}{year}"},
    {"label": "auth_year  (cui_2015)", "pattern": "{auth}_{year}"},
    {"label": "AuthEtAlYear  (CuiEtAl2015)", "pattern": "{AuthEtAl}{year}"},
    {"label": "auth:yearshorttitle  (cui:2015variational)", "pattern": "{auth}:{year}{title}"},
    {"label": "AuthYearShortTitle  (Cui2015VariationalMatrixProduct)", "pattern": "{Auth}{year}{ShortTitle}"},
]

_TOKEN = re.compile(r"\{([A-Za-z0-9]+)\}")


def ascii_alnum(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ß", "ss").replace("ø", "o").replace("Ø", "O").replace("ł", "l").replace("Ł", "L")
    return re.sub(r"[^A-Za-z0-9]", "", s)


def family_names(authors: str) -> list[str]:
    out = []
    for a in (authors or "").split(";"):
        a = a.strip()
        if not a:
            continue
        fam = a.split(",")[0].strip() if "," in a else a.split()[-1]
        out.append(ascii_alnum(fam))
    return [f for f in out if f]


def title_words(title: str) -> list[str]:
    words = re.split(r"[\s\-–—/:]+", re.sub(r"<[^>]+>", "", title or ""))
    clean = [ascii_alnum(w) for w in words]
    return [w for w in clean if w and w.lower() not in STOPWORDS]


def _case(value: str, token: str) -> str:
    if token.isupper() and len(token) > 1:
        return value.upper()
    if token[0].isupper():
        return value[:1].upper() + value[1:].lower()
    return value.lower()


def render(pattern: str, paper: dict) -> str:
    fams = family_names(paper.get("authors") or "")
    words = title_words(paper.get("title") or "")
    year = str(paper.get("year") or "")
    m = re.search(r"\d{4}", year)
    year = m.group(0) if m else ""

    def repl(m: re.Match) -> str:
        tok = m.group(1)
        low = tok.lower()
        if low == "auth":
            return _case(fams[0], tok) if fams else ""
        if low == "authetal":
            if not fams:
                return ""
            first = _case(fams[0], tok[0])
            if len(fams) == 1:
                return first
            if len(fams) == 2:
                return first + _case(fams[1], tok[0].upper() if tok[0].isupper() else "A")
            return first + "EtAl"
        if low == "authors2":
            return "".join(_case(f, tok[0] if i == 0 else "A") for i, f in enumerate(fams[:2]))
        if low == "year":
            return year
        if low == "yy":
            return year[-2:]
        if low == "title":
            return _case(words[0], tok) if words else ""
        if low == "shorttitle":
            if tok[0].isupper():
                return "".join(_case(w, "A") for w in words[:3])
            return "".join(w.lower() for w in words[:3])
        if low == "journal":
            j = [w for w in title_words(paper.get("journal") or "")]
            initials = "".join(w[0] for w in j)
            return initials.upper() if tok[0].isupper() else initials.lower()
        if low == "doi":
            doi = paper.get("doi") or ""
            return ascii_alnum(doi.split("/", 1)[-1]).lower()
        return m.group(0)  # unknown tokens are left visible so mistakes are obvious

    key = _TOKEN.sub(repl, pattern or "{auth}{year}")
    key = re.sub(r"[^A-Za-z0-9_:\-.+]", "", key)
    return key or "item"


def required_fields(pattern: str) -> set[str]:
    """Item fields the pattern draws on (used to wait until a new item is complete)."""
    need = set()
    for tok in _TOKEN.findall(pattern or ""):
        low = tok.lower()
        if low.startswith("auth"):
            need.add("authors")
        elif low in ("title", "shorttitle"):
            need.add("title")
        elif low in ("year", "yy"):
            need.add("year")
        elif low in ("journal", "doi"):
            need.add(low)
    return need


def make_unique(key: str, taken: set[str]) -> str:
    """Disambiguate with a, b, c … like Zotero/Better BibTeX."""
    if key not in taken:
        return key
    i = 0
    while True:
        suffix = ""
        n = i
        while True:
            suffix = chr(ord("a") + n % 26) + suffix
            n = n // 26 - 1
            if n < 0:
                break
        if key + suffix not in taken:
            return key + suffix
        i += 1


def generate(pattern: str, paper: dict, taken: set[str]) -> str:
    return make_unique(render(pattern, paper), taken)
