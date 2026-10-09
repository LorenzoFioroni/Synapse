"""Library search.

Three scopes, selectable per query:

* ``basic``    – title, abstract and authors
* ``all``      – every metadata field plus attachment names
* ``fulltext`` – everything above plus the text of attached PDFs (from the
                 separate full-text index)

Queries are split into terms (``"quoted phrases"`` stay together); every term
must match somewhere (AND). Matching ignores case and accents, so ``banuls``
finds ``Bañuls``. A leading ``-`` excludes a term.

Field filters narrow the results and combine with plain terms (all are ANDed,
any can be negated with ``-``):

    tag:quantum   tag:"open systems"   tag:none
    folder:Transport   folder:"Physics/Transport"   folder:none
    type:book   year:2020   year:2018-2022   year:>2015   year:<=2000
    has:pdf   has:doi   has:abstract   (any field: has:<field>)
    author:cirac   title:lindblad   journal:"physical review"   doi:10.1103   key:cui2015

Unknown prefixes (``arXiv:2205.03415``) are searched as plain text.
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


FILTER_KEYS = {"tag", "tags", "folder", "type", "year", "has", "author", "title", "journal", "doi", "key"}
FIELD_ALIASES = {"author": "authors", "key": "citation_key", "journal": "journal", "title": "title", "doi": "doi"}

_TOKEN = re.compile(r'(-?)(?:([A-Za-z_]+):)?(?:"([^"]*)"|(\S+))')


def parse_query(q: str) -> list[tuple]:
    """Return [(term, negated, field)]; ``field`` is None for plain text terms."""
    out = []
    for m in _TOKEN.finditer(q or ""):
        neg, field, quoted, bare = bool(m.group(1)), m.group(2), m.group(3), m.group(4)
        value = quoted if quoted is not None else bare
        if field and field.lower() not in FILTER_KEYS:
            value, field = f"{field}:{value}", None       # e.g. arXiv:2205.03415 is plain text
        if value in (None, "", "-"):
            continue
        out.append((value, neg, field.lower() if field else None))
    return out


def has_filters(q: str) -> bool:
    return any(f for _, _, f in parse_query(q))


_hay_cache: dict[tuple, str] = {}


def _haystack(paper: dict, mode: str) -> str:
    key = (paper["id"], paper.get("date_modified"), mode, len(paper.get("attachments") or []),
           tuple(paper.get("tags") or ()))
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
        parts += list(paper.get("tags") or [])
    return normalize(" \u0001 ".join(parts))


def _year_matches(value, spec: str) -> bool:
    m = re.search(r"\d{4}", str(value or ""))
    if not m:
        return False
    y = int(m.group(0))
    spec = spec.replace(" ", "")
    if r := re.fullmatch(r"(\d{4})-(\d{4})", spec):
        return int(r.group(1)) <= y <= int(r.group(2))
    if r := re.fullmatch(r"(>=|<=|>|<)(\d{4})", spec):
        op, v = r.group(1), int(r.group(2))
        return {">": y > v, "<": y < v, ">=": y >= v, "<=": y <= v}[op]
    return spec.isdigit() and y == int(spec)


def _filter_matches(p: dict, field: str, value: str, ctx: dict) -> bool:
    v = normalize(value)
    if field in ("tag", "tags"):
        tags = [normalize(t) for t in p.get("tags") or []]
        return not tags if v == "none" else v in tags
    if field == "folder":
        fids = p.get("folder_ids") or []
        if v in ("none", "unfiled"):
            return not fids
        want = [x.strip() for x in v.split("/") if x.strip()]
        for fid in fids:   # matches if ANY of the item's folders matches
            path = [normalize(n) for n in ctx.get("folder_paths", {}).get(fid, [])]
            if "/" in v:   # a path: must match from the root, e.g. "Physics/Transport"
                if path[:len(want)] == want:
                    return True
            elif v in path:
                return True
        return False
    if field == "type":
        key = p.get("item_type") or ""
        label = ctx.get("type_labels", {}).get(key, "")
        return normalize(key).startswith(v) or normalize(label).startswith(v) or v in normalize(label)
    if field == "year":
        return _year_matches(p.get("year"), value)
    if field == "has":
        if v in ("pdf", "attachment", "file"):
            return bool(p.get("attachments"))
        if v in ("tag", "tags"):
            return bool(p.get("tags"))
        if v in ("folder", "folders"):
            return bool(p.get("folder_ids"))
        key = FIELD_ALIASES.get(v, v)
        return str(p.get(key) or "").strip() != ""
    key = FIELD_ALIASES.get(field, field)
    return v in normalize(p.get(key) or "")


def search(papers: list[dict], query: str, mode: str = "basic", fulltext=None,
           ctx: dict | None = None) -> tuple[list[dict], dict]:
    """Filter ``papers``. Returns (matches, hits) where hits[paper_id] = {"fulltext": snippet}.

    ``ctx`` gives filters what they need: ``folder_paths`` {folder_id: [names]}
    and ``type_labels`` {item_type: label}.
    """
    if mode not in MODES:
        mode = "basic"
    ctx = ctx or {}
    parsed = parse_query(query)
    if not parsed:
        return papers, {}
    filters = [(v, neg, f) for v, neg, f in parsed if f]
    terms = [(v, neg) for v, neg, f in parsed if not f]

    if filters:
        papers = [p for p in papers
                  if all(_filter_matches(p, f, v, ctx) != neg for v, neg, f in filters)]
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
