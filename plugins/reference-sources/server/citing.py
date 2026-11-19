"""The works citing a work, as OpenAlex or Semantic Scholar know them (for get_citing_works).

Papers with a journal DOI are asked of OpenAlex first: it sorts and filters on its side and gives
the total. Preprints known only by their arXiv id are asked of Semantic Scholar first, which counts
a preprint and its journal version as one paper (OpenAlex often keeps them apart, the preprint with
few citations). Each is the other's fallback when it does not know the work or knows no citing one.
"""

from __future__ import annotations

import logging

from backend.plugins import PluginError, scholarly

from .client import Client, FetchFailed, Stopped
from .providers import OPENALEX_API, S2_API, _q
from .records import openalex_work

log = logging.getLogger(__name__)
OPENALEX_PAGE = 200  # OpenAlex's largest page
S2_PAGE = 1000  # Semantic Scholar's largest page; it cannot sort, so this is what gets sorted
OPENALEX_FIELDS = "id,doi,title,display_name,publication_year,authorships,primary_location,locations,ids,cited_by_count"
S2_FIELDS = "title,year,publicationDate,authors,venue,externalIds,citationCount"


def openalex(doi: str | None, *, limit: int, sort: str, since_year: int | None, http: Client) -> dict | None:
    if not doi:
        return None
    r = http.get(f"{OPENALEX_API}/works/doi:{_q(doi)}", params={"select": "id"})
    wid = ((r.json().get("id") or "") if r is not None else "").rsplit("/", 1)[-1]
    if not wid:
        return None
    flt = f"cites:{wid}" + (f",publication_year:>{since_year - 1}" if since_year else "")
    works: list[dict] = []
    total = None
    page = 1
    while len(works) < limit:
        rr = http.get(
            f"{OPENALEX_API}/works",
            params={
                "filter": flt,
                "sort": "publication_date:desc" if sort == "recent" else "cited_by_count:desc",
                "per-page": min(OPENALEX_PAGE, limit),
                "page": page,
                "select": OPENALEX_FIELDS,
            },
        )
        d = rr.json() if rr is not None else {}
        total = (d.get("meta") or {}).get("count", total)
        rows = d.get("results") or []
        works += [{**openalex_work(w), "cited_by_count": w.get("cited_by_count")} for w in rows]
        if len(rows) < min(OPENALEX_PAGE, limit):
            break
        page += 1
    return {"source": "OpenAlex", "works": works[:limit], "total": total if isinstance(total, int) else None}


def semantic_scholar(
    doi: str | None, arxiv_id: str | None, *, limit: int, sort: str, since_year: int | None, http: Client
) -> dict | None:
    pid = f"DOI:{doi}" if doi and not scholarly.ARXIV_DOI.match(doi) else f"arXiv:{arxiv_id}" if arxiv_id else None
    pid = pid or (f"DOI:{doi}" if doi else None)
    if not pid:
        return None
    r = http.get(f"{S2_API}/paper/{_q(pid)}/citations", params={"fields": S2_FIELDS, "limit": S2_PAGE})
    if r is None:
        return None
    rows = [row.get("citingPaper") or {} for row in r.json().get("data") or []]
    total = len(rows)
    note = None
    if len(rows) >= S2_PAGE:  # only the paper's record says how many there are
        c = http.get(f"{S2_API}/paper/{_q(pid)}", params={"fields": "citationCount"})
        count = c.json().get("citationCount") if c is not None else None
        total = count if isinstance(count, int) and count > total else total
        if total > len(rows):
            note = f"Semantic Scholar sends {S2_PAGE} citing works at most: they are sorted among those."
    works: list[dict] = []
    for p in rows:
        ext = p.get("externalIds") or {}
        if since_year and (p.get("year") or 0) < since_year:
            continue
        works.append(
            {
                "doi": ext.get("DOI"),
                "arxiv_id": ext.get("ArXiv"),
                "s2_id": p.get("paperId"),
                "title": p.get("title"),
                "year": p.get("year"),
                "venue": p.get("venue"),
                "authors": scholarly.join_authors(
                    [scholarly.parse_display_name(a.get("name") or "") for a in p.get("authors") or []]
                ),
                "cited_by_count": p.get("citationCount"),
                "_date": p.get("publicationDate") or str(p.get("year") or ""),
            }
        )
    if sort == "recent":
        works.sort(key=lambda w: str(w["_date"]), reverse=True)
    else:
        works.sort(key=lambda w: w["cited_by_count"] or 0, reverse=True)
    for w in works:
        del w["_date"]
    out = {"source": "Semantic Scholar", "works": works[:limit], "total": total}
    return {**out, "note": note} if note else out


def citing(query: dict, *, http: Client) -> dict | None:
    """The works citing the queried work, from the first service that knows some; None when neither
    knows the work. Raises when every service failed."""
    http.ensure_open()
    doi = scholarly.norm_doi(query.get("doi"))
    arx = scholarly.norm_arxiv(query.get("arxiv"))
    if not doi and not arx:
        raise PluginError("This work has no DOI or arXiv ID to look up the works citing it.")
    limit = max(1, min(int(query.get("limit") or 50), 1000))
    sort = "recent" if query.get("sort") == "recent" else "cited"
    since = query.get("since_year") if isinstance(query.get("since_year"), int) else None
    journal = doi if doi and not scholarly.ARXIV_DOI.match(doi) else None
    attempts = [
        (
            "openalex",
            lambda: openalex(
                doi or (f"10.48550/arxiv.{arx}" if arx else None), limit=limit, sort=sort, since_year=since, http=http
            ),
        ),
        ("semanticscholar", lambda: semantic_scholar(doi, arx, limit=limit, sort=sort, since_year=since, http=http)),
    ]
    if not journal:
        attempts.reverse()
    first, errors = None, []
    for name, run in attempts:
        try:
            found = run()
        except Stopped:
            raise
        except Exception as e:  # a failing service must not hide the next one
            log.info("Citing works from %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
            continue
        if found and found["works"]:
            http.ensure_open()
            return found
        first = first or found
    http.ensure_open()
    if first is None and errors:
        raise FetchFailed("Could not get the citing works (" + "; ".join(errors) + ").")
    return first
