"""Library search.

Three scopes, selectable per query:

* ``basic``    – title, abstract and authors
* ``all``      – every metadata field plus attachment names
* ``fulltext`` – everything above plus the text of attached PDFs (from the
                 separate full-text index)

Queries are split into terms (``"quoted phrases"`` stay together); every term
must match somewhere (AND). Matching ignores case and accents, so ``banuls``
finds ``Bañuls``. A leading ``-`` excludes a term.
"""
from __future__ import annotations

import re
import unicodedata

from .fields import ALL_SEARCH_KEYS, BASIC_SEARCH_KEYS

MODES = ("basic", "all", "fulltext")


def normalize(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.casefold().split())


def parse_query(q: str) -> list[tuple[str, bool]]:
    """Return [(term, negated)] with quoted phrases kept intact."""
    out = []
    for m in re.finditer(r'(-?)"([^"]+)"|(-?)(\S+)', q or ""):
        neg = bool(m.group(1) or m.group(3))
        term = m.group(2) if m.group(2) is not None else m.group(4)
        if term in ("-", ""):
            continue
        out.append((term, neg))
    return out


_hay_cache: dict[tuple, str] = {}


def _haystack(paper: dict, mode: str) -> str:
    key = (paper["id"], paper.get("date_modified"), mode, len(paper.get("attachments") or []))
    hit = _hay_cache.get(key)
    if hit is None:
        if len(_hay_cache) > 50000:
            _hay_cache.clear()
        hit = _hay_cache[key] = _build_haystack(paper, mode)
    return hit


def _build_haystack(paper: dict, mode: str) -> str:
    keys = BASIC_SEARCH_KEYS if mode == "basic" else ALL_SEARCH_KEYS
    parts = [str(paper.get(k) or "") for k in keys]
    if mode != "basic":
        parts += [a.get("name") or "" for a in paper.get("attachments") or []]
    return normalize(" \u0001 ".join(parts))


def search(papers: list[dict], query: str, mode: str = "basic", fulltext=None) -> tuple[list[dict], dict]:
    """Filter ``papers``. Returns (matches, hits) where hits[paper_id] = {"fulltext": snippet}."""
    if mode not in MODES:
        mode = "basic"
    terms = parse_query(query)
    if not terms:
        return papers, {}

    ft_matches: dict[str, dict[str, str]] = {}
    if mode == "fulltext" and fulltext is not None:
        for term, neg in terms:
            ft_matches[term] = fulltext.match(term)

    hay = {p["id"]: _haystack(p, mode) for p in papers}
    results, hits = [], {}
    for p in papers:
        h = hay[p["id"]]
        ok = True
        snippet = None
        for term, neg in terms:
            in_meta = normalize(term) in h
            in_text = p["id"] in ft_matches.get(term, {})
            found = in_meta or in_text
            if neg:
                if found:
                    ok = False
                    break
                continue
            if not found:
                ok = False
                break
            if in_text and not in_meta and snippet is None:
                snippet = ft_matches[term][p["id"]]
        if ok:
            results.append(p)
            if snippet is None and mode == "fulltext":
                # Show context even when the metadata also matched.
                for term, neg in terms:
                    if not neg and p["id"] in ft_matches.get(term, {}):
                        snippet = ft_matches[term][p["id"]]
                        break
            if snippet:
                hits[p["id"]] = {"fulltext": snippet}
    return results, hits
