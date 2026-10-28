"""Reference lists from the web: the works an item cites, as Semantic Scholar, OpenAlex or Crossref
know them.

Sources, tried in order until one returns references:
  1. Semantic Scholar  (DOI or arXiv id; one request, titles included; good for arXiv)
  2. OpenAlex          (DOI; referenced works + their details; an API key raises the daily quota)
  3. Crossref          (DOI; the publisher's reference list, often with DOIs only)
  4. arXiv HTML        (arXiv id; the bibliography of the paper's HTML version, see arxiv_html.py)
"""

from __future__ import annotations

import logging
import re
from datetime import date
from urllib.parse import quote

from backend.plugins import PluginError, scholarly

from . import arxiv_html
from .client import Client, FetchFailed, Stopped
from .records import openalex_work, usable

ARXIV_DOI = scholarly.ARXIV_DOI
identifiers = scholarly.identifiers
join_authors = scholarly.join_authors
parse_display_name = scholarly.parse_display_name
log = logging.getLogger(__name__)
S2_API = "https://api.semanticscholar.org/graph/v1"
OPENALEX_API = "https://api.openalex.org"
CROSSREF_API = "https://api.crossref.org"


def _q(s: str) -> str:
    """Quote an identifier for a URL path (old DOIs can contain '#', ';', '<' …)."""
    return quote(s, safe="/:")


def semantic_scholar(doi: str | None, arxiv_id: str | None, *, http: Client) -> list[dict]:
    """Try the DOI first (a journal version), then the arXiv id."""
    ids = []
    if doi and not ARXIV_DOI.match(doi):
        ids.append(f"DOI:{doi}")
    if arxiv_id:
        ids.append(f"arXiv:{arxiv_id}")
    elif doi and ARXIV_DOI.match(doi):
        ids.append(f"DOI:{doi}")
    for pid in ids:
        refs = _s2_references(pid, http=http)
        if refs:
            return refs
    return []


def _s2_references(pid: str, *, http: Client) -> list[dict]:
    out, offset = [], 0
    visited = set()
    while True:
        if offset in visited:
            raise PluginError("Semantic Scholar repeated a references page.")
        visited.add(offset)
        r = http.get(
            f"{S2_API}/paper/{_q(pid)}/references",
            params={"fields": "title,year,authors,venue,externalIds", "limit": 1000, "offset": offset},
        )
        if r is None:
            return []
        d = r.json()
        for row in d.get("data") or []:
            p = row.get("citedPaper") or {}
            if not p.get("title") and not p.get("externalIds"):
                continue
            ext = p.get("externalIds") or {}
            out.append(
                {
                    "doi": ext.get("DOI"),
                    "arxiv_id": ext.get("ArXiv"),
                    "s2_id": p.get("paperId"),
                    "title": p.get("title"),
                    "year": p.get("year"),
                    "venue": p.get("venue"),
                    "authors": join_authors([parse_display_name(a.get("name") or "") for a in p.get("authors") or []]),
                }
            )
        if d.get("next") is None or not d.get("data"):
            return out
        offset = d["next"]


def openalex(doi: str | None, *, http: Client) -> list[dict]:
    if not doi:
        return []
    params = {"select": "id,referenced_works"}
    r = http.get(f"{OPENALEX_API}/works/doi:{_q(doi)}", params=params)
    if r is None:
        return []
    wids = [w.rsplit("/", 1)[-1] for w in r.json().get("referenced_works") or []]
    details: dict[str, dict] = {}
    for i in range(0, len(wids), 50):  # 50 ids per request (OpenAlex's OR-filter limit)
        chunk = wids[i : i + 50]
        p = {
            "filter": "openalex:" + "|".join(chunk),
            "per-page": 50,
            "select": "id,doi,title,display_name,publication_year,authorships,primary_location,locations,ids",
        }
        rr = http.get(f"{OPENALEX_API}/works", params=p)
        for w in (rr.json().get("results") or []) if rr is not None else []:
            details[(w.get("id") or "").rsplit("/", 1)[-1]] = openalex_work(w)
    return [details.get(w) or {"openalex_id": w} for w in wids]  # keep the original order


def crossref(doi: str | None, *, http: Client) -> list[dict]:
    if not doi or ARXIV_DOI.match(doi):
        return []
    r = http.get(f"{CROSSREF_API}/works/{_q(doi)}")
    if r is None:
        return []
    out = []
    for ref in (r.json().get("message") or {}).get("reference") or []:
        out.append(
            {
                "doi": ref.get("DOI"),
                "title": ref.get("article-title") or ref.get("volume-title"),
                "authors": ref.get("author"),
                "year": (re.search(r"\d{4}", ref.get("year") or "") or [None])[0],
                "venue": ref.get("journal-title") or ref.get("series-title"),
                "raw": ref.get("unstructured"),
            }
        )
    return out


def recent_arxiv(arxiv_id: str | None, today: date | None = None) -> bool:
    """An arXiv id from this month or the last (new-style ids start with the year and month)."""
    m = re.match(r"^(\d{2})(\d{2})\.\d{4,5}", arxiv_id or "")
    if not m:
        return False
    today = today or date.today()
    months = (today.year - 2000 - int(m.group(1))) * 12 + today.month - int(m.group(2))
    return 0 <= months <= 1


def fetch(item: dict, *, http: Client, fresh: bool = False) -> tuple[str, list[dict]]:
    """The reference list of ``item``: (source name, references). Raises PluginError when the
    item has no DOI or arXiv id; returns ("none", []) when no source knows its references.

    The other services often know only part of a new arXiv paper's references in its first days
    (or none), while its HTML version has them all. So for an arXiv paper from this month or the
    last, the HTML list is asked too and kept when it is longer; with ``fresh`` (a paper announced
    in the last days) it is asked first, and the others only when it has none."""
    http.ensure_open()
    doi, arx = identifiers(item)
    if not doi and not arx:
        raise PluginError("This item has no DOI or arXiv ID to look up its references with.")
    attempts = [
        ("semanticscholar", lambda: semantic_scholar(doi, arx, http=http)),
        (
            "openalex",
            lambda: openalex(doi or (f"10.48550/arxiv.{arx}" if arx else None), http=http),
        ),
        ("crossref", lambda: crossref(doi, http=http)),
    ]
    if arx:
        html = ("arxiv-html", lambda: arxiv_html.fetch(arx, http=http))
        attempts = [html, *attempts] if fresh else [*attempts, html]
    errors = []
    for name, run in attempts:
        try:
            refs = usable(run())
        except Stopped:
            raise
        except Exception as e:  # a failing service must not hide the next one
            log.info("References from %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
            continue
        if not refs:
            continue
        if arx and name != "arxiv-html" and not fresh and recent_arxiv(arx):
            try:
                longer = usable(arxiv_html.fetch(arx, http=http))
            except Stopped:
                raise
            except Exception as e:  # the list in hand is still good
                log.info("References from arxiv-html failed: %s", e)
                longer = []
            if len(longer) > len(refs):
                http.ensure_open()
                return "arxiv-html", longer
        http.ensure_open()
        return name, refs
    http.ensure_open()
    if errors:  # an answer of "no references" only counts when every service answered
        raise FetchFailed("Could not get the references (" + "; ".join(errors) + ").")
    return "none", []
