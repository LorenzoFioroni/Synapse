"""DOIs and arXiv identifiers: cleaning them up, normalising them for matching, and finding them in an item.

Used by the online lookups, the citation graph and the MCP tools, so that all of them agree on what
counts as the same paper.
"""

from __future__ import annotations

import re
import unicodedata

# An arXiv DOI (10.48550/arXiv.2101.00001) names the arXiv preprint.
ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)
# New-style (2101.00001) and old-style (hep-th/9901001) arXiv ids, without the version.
ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I)


def clean_doi(doi: str | None) -> str:
    """A DOI without its ``https://doi.org/`` or ``doi:`` prefix (case kept)."""
    doi = (doi or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    return re.sub(r"^doi:\s*", "", doi, flags=re.I).strip()


def norm_doi(doi: str | None) -> str | None:
    """A DOI for matching: no prefix, no trailing dot, lower case; None if empty."""
    if not doi:
        return None
    d = clean_doi(str(doi).strip())
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d, flags=re.I).strip().rstrip(".")
    return d.lower() or None


def norm_arxiv(aid: str | None) -> str | None:
    """An arXiv id for matching: the bare id without version, lower case; None if there is none."""
    if not aid:
        return None
    m = ARXIV_ID.search(str(aid))
    return m.group(1).lower() if m else None


def arxiv_id_of(item: dict) -> str | None:
    """The arXiv identifier of an existing item, if it records one anywhere."""
    for field in ("archive_id", "doi", "url", "extra", "journal", "number"):
        v = str(item.get(field) or "")
        m = re.search(r"(?:arxiv[:./\s]*|arxiv\.org/(?:abs|pdf)/|10\.48550/arxiv\.)" + ARXIV_ID.pattern, v, re.I)
        if m:
            return m.group(1)
    return None


def identifiers(item: dict) -> tuple[str | None, str | None]:
    """(doi, arXiv id) of a library item, normalised; an arXiv DOI counts as its arXiv id."""
    doi = norm_doi(item.get("doi"))
    arx = norm_arxiv(arxiv_id_of(item))
    if doi and (m := ARXIV_DOI.match(doi)):
        arx = arx or norm_arxiv(m.group(1))
    return doi, arx


def title_key(title: str | None) -> str | None:
    """A title reduced for matching: no accents, case, punctuation, LaTeX or spacing differences.
    Very short titles ("Introduction") are too ambiguous and give None."""
    t = unicodedata.normalize("NFKD", str(title or ""))
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    t = re.sub(r"<[^>]+>|\\[a-z]+", " ", t)  # markup tags, LaTeX commands
    t = " ".join(re.sub(r"[\W_]+", " ", t).split())
    return t if len(t) >= 15 else None
