"""Attachment file-name template rendering.

Template tokens (case sensitive):
  {firstCreator}  Smith | Smith and Jones | Smith et al.
  {authors}       up to three last names joined with ', ' (+ 'et al.')
  {year}          4-digit year
  {title}         title (LaTeX rendered, truncated to N words)
  {shortTitle}    short title, or the title up to the first colon
  {journal}       publication title        {journalAbbrev}  journal abbreviation
  {citekey}       Better BibTeX citation key (from Extra "Citation Key: ...")
  {doi}           DOI with '/' replaced by '_'
  {key}           Zotero item key          {itemType}      item type
If a token is empty, the literal text that follows it is dropped too, so
"{firstCreator} - {year} - {title}" never produces "Smith -  - Title".
"""
from __future__ import annotations

import re
import unicodedata

from . import latex
from . import textutil as tu

TOKEN = re.compile(r"\{(\w+)\}")
FORBIDDEN = re.compile(r'[\\/*?"<>|\x00-\x1f]')


def _lastnames(creators, primary=("author", "inventor", "programmer", "artist", "director", "presenter", "podcaster")):
    cr = [c for c in creators or [] if c.get("creatorType", "author") in primary]
    if not cr:
        cr = [c for c in creators or [] if c.get("creatorType") == "editor"]
    return [tu.plain(c.get("lastName") or c.get("name") or "") for c in cr if (c.get("lastName") or c.get("name"))]


def tokens(data: dict, meta: dict | None = None, max_words: int = 12) -> dict:
    meta = meta or {}
    ln = _lastnames(data.get("creators"))
    if not ln:
        first = ""
    elif len(ln) == 1:
        first = ln[0]
    elif len(ln) == 2:
        first = f"{ln[0]} and {ln[1]}"
    else:
        first = f"{ln[0]} et al."
    authors = ", ".join(ln[:3]) + (" et al." if len(ln) > 3 else "")
    y = re.search(r"\d{4}", meta.get("parsedDate") or data.get("date") or "")
    title = tu.plain(data.get("title", ""))
    words = title.split()
    if len(words) > max_words:
        title = " ".join(words[:max_words]).rstrip(",;:") + "…"
    short = tu.plain(data.get("shortTitle") or "") or tu.plain(data.get("title", "")).split(":")[0]
    ck = re.search(r"^\s*Citation Key:\s*(\S+)", data.get("extra", "") or "", re.M | re.I)
    pub = data.get("publicationTitle") or data.get("proceedingsTitle") or data.get("bookTitle") or ""
    return {"firstCreator": first, "authors": authors, "year": y.group(0) if y else "", "title": title,
            "shortTitle": short, "journal": tu.plain(pub), "journalAbbrev": tu.plain(data.get("journalAbbreviation", "")),
            "citekey": ck.group(1) if ck else "", "doi": tu.clean_doi(data.get("DOI", "")).replace("/", "_"),
            "key": data.get("key", ""), "itemType": data.get("itemType", "")}


def render(template: str, toks: dict) -> str:
    """Literal text between two tokens is a separator: it is emitted only when
    non-empty tokens stand on both sides of it."""
    parts = TOKEN.split(template)  # L0, T1, L1, T2, L2, ...
    out = [parts[0]]
    have = False
    for i in range(1, len(parts), 2):
        v = toks.get(parts[i], "")
        if v:
            if have:
                out.append(parts[i - 1] if i - 1 > 0 else "")
            out.append(v)
            have = True
    if len(parts) > 1:
        out.append(parts[-1])  # suffix
    return "".join(out)


def sanitize(name: str, ascii_only: bool = True, max_len: int = 120) -> str:
    s = name.replace(" ", " ")
    s = re.sub(r"\s*:\s*", " - ", s)
    s = s.replace("…", "...")
    if ascii_only:
        s = "".join(latex.MATH[c].lstrip("\\") if c in latex.MATH and latex.MATH[c].lstrip("\\").isalpha() else c for c in s)
        s = tu.strip_accents(s)
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    else:
        s = unicodedata.normalize("NFC", s)
    s = FORBIDDEN.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" .-_")
    if len(s) > max_len:
        cut = s[:max_len]
        if " " in cut[max_len // 2:]:
            cut = cut[:cut.rfind(" ")]
        s = cut.rstrip(" .,-_;")
    return s or "attachment"


def filename_for(data: dict, meta: dict | None, current: str, settings) -> str:
    cfg = settings.get("pdf")
    ext = ""
    m = re.search(r"(\.[A-Za-z0-9]{1,5})$", current or "")
    if m:
        ext = m.group(1).lower()
    else:
        ext = ".pdf"
    stem = render(cfg.get("filename_template") or "{firstCreator} - {year} - {title}",
                  tokens(data, meta, int(cfg.get("max_title_words") or 12)))
    return sanitize(stem, bool(cfg.get("ascii_filenames", True)), int(cfg.get("max_length") or 120)) + ext
