"""Normalize reference records and provider metadata without HTTP or activation state."""

import re

from backend.plugins import scholarly

ARXIV_DOI = scholarly.ARXIV_DOI
ARXIV_ID = scholarly.ARXIV_ID
REF_FIELDS = scholarly.REF_FIELDS
norm_doi = scholarly.norm_doi
norm_arxiv = scholarly.norm_arxiv
join_authors = scholarly.join_authors
parse_display_name = scholarly.parse_display_name


def clean_ref(r: dict) -> dict:
    out = {k: r.get(k) for k in REF_FIELDS}
    out["doi"] = norm_doi(out["doi"])
    out["arxiv_id"] = norm_arxiv(out["arxiv_id"])
    if out["doi"] and not out["arxiv_id"] and (m := ARXIV_DOI.match(out["doi"])):
        out["arxiv_id"] = norm_arxiv(m.group(1))
    try:
        out["year"] = int(out["year"]) if out["year"] else None
    except (TypeError, ValueError):
        out["year"] = None
    for k in ("title", "authors", "venue", "raw"):
        out[k] = " ".join(str(out[k]).split()) if out[k] else None
    return out


def openalex_work(w: dict) -> dict:
    ids = w.get("ids") or {}
    arx = None
    for loc in [w.get("primary_location") or {}] + list(w.get("locations") or []):
        m = re.search(r"arxiv\.org/(?:abs|pdf)/" + ARXIV_ID.pattern, (loc or {}).get("landing_page_url") or "", re.I)
        if m:
            arx = m.group(1)
            break
    src = (w.get("primary_location") or {}).get("source") or {}
    return {
        "doi": w.get("doi") or ids.get("doi"),
        "arxiv_id": arx,
        "openalex_id": (w.get("id") or "").rsplit("/", 1)[-1] or None,
        "title": w.get("title") or w.get("display_name"),
        "year": w.get("publication_year"),
        "venue": src.get("display_name"),
        "authors": join_authors(
            [parse_display_name((a.get("author") or {}).get("display_name") or "") for a in w.get("authorships") or []]
        ),
    }


def usable(refs: list[dict]) -> list[dict]:
    refs = [clean_ref(r) for r in refs]
    return [r for r in refs if any(r[k] for k in ("doi", "arxiv_id", "openalex_id", "s2_id", "title", "raw"))]
