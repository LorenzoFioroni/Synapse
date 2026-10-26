"""Normalize metadata queries and authors; running plugins supply the online providers."""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Callable

from ..formats.common import join_authors, split_authors
from ..library import citekeys
from ..library.identifiers import ARXIV_DOI, arxiv_id_of, clean_doi
from .http import LookupFailed


# ------------------------------------------------------------ identifiers
def classify_identifier(s: str) -> tuple[str, str]:
    s = (s or "").strip()
    m = re.search(r"(10\.\d{4,9}/\S+)", s)
    if m:
        return "doi", clean_doi(m.group(1)).rstrip(".")
    m = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)?(\d{4}\.\d{4,5})(v\d+)?", s, re.I)
    if m:
        return "arxiv", m.group(1)
    m = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)([a-z\-]+(?:\.[A-Z]{2})?/\d{7})", s, re.I)
    if m:
        return "arxiv", m.group(1)
    digits = re.sub(r"[^0-9Xx]", "", s)
    if len(digits) in (10, 13) and re.fullmatch(r"[\d\-\sXx]+", s.replace("ISBN", "").replace(":", "").strip()):
        return "isbn", digits.upper()
    m = re.fullmatch(r"(?:PMID:?\s*)?(\d{5,9})", s, re.I)
    if m:
        return "pmid", m.group(1)
    raise LookupFailed("Not a recognised identifier. Try a DOI, arXiv ID or ISBN.")


# ------------------------------------------------------- author resolution
def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def _given_compatible(fetched: str, known: str) -> bool:
    fp = [p for p in re.split(r"[\s.\-]+", fetched or "") if p]
    kp = [p for p in re.split(r"[\s.\-]+", known or "") if p]
    return bool(fp) and len(fp) == len(kp) and all(_plain(a) == _plain(b) for a, b in zip(fp, kp))


def resolve_authors(pairs: list[tuple[str, str]], known: list[tuple[str, str]]) -> str:
    """Prefer the accented spelling already in the library (Banuls -> Bañuls)."""
    out = []
    for fam, giv in pairs:
        best = None
        for kf, kg in known:
            if _plain(kf) == _plain(fam) and _given_compatible(giv, kg):
                cand = f"{kf}, {kg}"
                if re.search(r"[^\x00-\x7f]", cand) and not re.search(r"[^\x00-\x7f]", f"{fam}, {giv}"):
                    best = (kf, kg)
                    break
        out.append(best or (fam, giv))
    return join_authors(out)


def known_authors(papers: list[dict]) -> list[tuple[str, str]]:
    seen = set()
    out = []
    for p in papers:
        for pair in split_authors(p.get("authors")):
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
    return out


TITLE_MATCH = 0.8


def title_similarity(a: str, b: str) -> float:
    """0..1, ignoring case, accents, punctuation and spacing.

    Uses difflib's matching blocks, so an extra or missing word ("The …", a subtitle) only
    lowers the score a little. (The old character-by-position comparison dropped to ~0 as
    soon as the two titles were shifted by one character.)
    """
    pa, pb = _plain(a), _plain(b)
    if not pa or not pb:
        return 0.0
    return difflib.SequenceMatcher(None, pa, pb, autojunk=False).ratio()


def lookup(
    *,
    identifier: str | None = None,
    doi: str | None = None,
    title: str | None = None,
    arxiv_id: str | None = None,
    known: list[tuple[str, str]] | None = None,
    email: str = "",
    sources: Callable[[dict], dict] | None = None,
) -> dict:
    """Normalize a query and ask the provided metadata sources for a matching item."""
    doi = clean_doi(doi) if doi else None
    if doi and (m := ARXIV_DOI.match(doi)):
        arxiv_id, doi = arxiv_id or m.group(1), None
    kind: str
    value: str | None
    if doi:
        kind, value = "doi", doi
    elif arxiv_id:
        kind, value = "arxiv", arxiv_id
    elif identifier:
        kind, value = classify_identifier(identifier)
        if kind == "doi" and (m := ARXIV_DOI.match(value)):
            kind, value = "arxiv", m.group(1)
    else:
        kind, value = "title", title
    if not value:
        raise LookupFailed("Nothing to look up: the item needs a DOI or a title.")
    if sources is None:
        raise LookupFailed("No metadata sources are on: turn on Metadata Sources in Settings → Plugins.")
    item = dict(sources({"kind": kind, "value": value, "email": email}))
    if known and item.get("authors"):
        item["authors"] = resolve_authors(split_authors(item["authors"]), known)
    return item


LOOKUP_FIELDS = (
    "item_type",
    "title",
    "authors",
    "year",
    "journal",
    "volume",
    "issue",
    "pages",
    "publisher",
    "place",
    "doi",
    "url",
    "issn",
    "isbn",
    "citation_key",
    "short_title",
    "language",
    "license",
    "archive",
    "archive_id",
    "library_catalog",
    "abstract",
    "extra",
)


def is_blank(v: object) -> bool:
    return v is None or str(v).strip() == ""


def compute_changes(current: dict, fetched: dict) -> list[dict]:
    """Changed fields between current item and fetched metadata, exactly matching frontend computeChanges."""
    changes = []
    for k in LOOKUP_FIELDS:
        f_val = fetched.get(k)
        if is_blank(f_val):
            continue
        c_val = current.get(k)
        c_str = "" if c_val is None else str(c_val)
        f_str = str(f_val)
        if f_str == c_str:
            continue
        if k == "item_type" and is_blank(c_val):
            continue
        has_current_key = k == "citation_key" and not is_blank(c_val)
        changes.append(
            {
                "key": k,
                "current": c_val,
                "fetched": f_val,
                "is_new": is_blank(c_val),
                "isNew": is_blank(c_val),
                "checked": not has_current_key,
            }
        )
    return changes


def lookup_for_item(services, item_or_id: str | dict) -> tuple[dict, dict]:
    """Fetch fresh metadata for an existing item (passes DOI, arXiv ID and title together, generates citation key)."""
    item = services.papers.get(item_or_id) if isinstance(item_or_id, str) else item_or_id
    if not item:
        raise LookupFailed("Item not found")
    item_id = item.get("id")
    others = [p for p in services.papers.list() if p.get("id") != item_id]

    fetched = lookup(
        sources=services.plugins.lookup_metadata,
        doi=item.get("doi"),
        title=item.get("title"),
        arxiv_id=arxiv_id_of(item),
        known=known_authors(others),
        email=services.settings.get("contact_email"),
    )
    merged = {**item, **fetched}
    taken = services.papers.all_citation_keys(exclude_id=item_id) if item_id else set()
    fetched["citation_key"] = citekeys.generate(services.settings.get("citekey_pattern"), merged, taken)
    return item, fetched
