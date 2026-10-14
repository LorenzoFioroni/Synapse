"""Helpers shared by the import/export formats.

Every format converts to and from the same intermediate representation: a
plain dict keyed by the field names in ``backend.library.fields`` (authors are stored
as ``"Family, Given; Family2, Given2"``).
"""
from __future__ import annotations

import re


def split_authors(authors: str | None) -> list[tuple[str, str]]:
    """'Cui, Jian; Cirac, J. Ignacio' -> [('Cui', 'Jian'), ('Cirac', 'J. Ignacio')]"""
    out = []
    for a in (authors or "").split(";"):
        a = a.strip()
        if not a:
            continue
        if "," in a:
            fam, giv = a.split(",", 1)
            out.append((fam.strip(), giv.strip()))
        else:
            out.append((a, ""))
    return out


def join_authors(pairs) -> str:
    parts = []
    for fam, giv in pairs:
        fam, giv = (fam or "").strip(), (giv or "").strip()
        if fam or giv:
            parts.append(f"{fam}, {giv}" if giv else fam)
    return "; ".join(parts)


PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "du", "la", "le", "ter", "ten", "dos", "das", "do"}


def parse_display_name(name: str) -> tuple[str, str]:
    """'J. Ignacio Cirac' -> ('Cirac', 'J. Ignacio'); 'Cirac, J. I.' -> ('Cirac', 'J. I.')."""
    name = " ".join((name or "").split())
    if not name:
        return "", ""
    if "," in name:
        fam, giv = name.split(",", 1)
        return fam.strip(), giv.strip()
    parts = name.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in PARTICLES:
        i -= 1
    return " ".join(parts[i:]), " ".join(parts[:i])


def year_from(value) -> str:
    m = re.search(r"(1[5-9]\d\d|20\d\d|21\d\d)", str(value or ""))
    return m.group(1) if m else ""


def split_pages(pages: str | None) -> tuple[str, str]:
    p = (pages or "").replace("--", "-").replace("–", "-").strip()
    if "-" in p:
        a, b = p.split("-", 1)
        return a.strip(), b.strip()
    return p, ""


def join_pages(start: str | None, end: str | None) -> str:
    start, end = (start or "").strip(), (end or "").strip()
    return f"{start}-{end}" if start and end and start != end else (start or end)


def split_keywords(kw: str | None) -> list[str]:
    return [k.strip() for k in re.split(r"[;,]\s*|\n", kw or "") if k.strip()]


def strip_tags(s: str | None) -> str:
    """Crossref abstracts come as JATS XML; keep the text only."""
    s = re.sub(r"<jats:title>.*?</jats:title>", "", s or "", flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    return " ".join(s.split())
