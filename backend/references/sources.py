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
import time

import requests

from ..formats.common import join_authors, parse_display_name
from ..library.identifiers import ARXIV_DOI, ARXIV_ID, identifiers, norm_arxiv, norm_doi
from ..online.http import TIMEOUT, LookupFailed, api_headers
from . import arxiv_html
from .store import REF_FIELDS

log = logging.getLogger(__name__)

S2_API = "https://api.semanticscholar.org/graph/v1"
OPENALEX_API = "https://api.openalex.org"
CROSSREF_API = "https://api.crossref.org"


class FetchFailed(LookupFailed):
    """The online services could not be reached / answered with errors (unlike an item
    without identifiers, trying again later may work)."""


def _clean_ref(r: dict) -> dict:
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


def _get(url: str, *, params=None, headers=None, retries: int = 1):
    """GET with one retry on 429/5xx. Returns the response, or None for 404."""
    for attempt in range(retries + 1):
        r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        if r.status_code == 404:
            return None
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            try:
                wait = float(r.headers.get("Retry-After") or 2)
            except ValueError:  # an HTTP date
                wait = 2
            time.sleep(min(max(wait, 0), 10))
            continue
        r.raise_for_status()
        return r
    return None


def _q(s: str) -> str:
    """Quote an identifier for a URL path (old DOIs can contain '#', ';', '<' …)."""
    return requests.utils.quote(s, safe="/:")


def semantic_scholar(doi: str | None, arxiv_id: str | None, *, email: str = "", api_key: str = "") -> list[dict]:
    """Try the DOI first (a journal version), then the arXiv id."""
    ids = []
    if doi and not ARXIV_DOI.match(doi):
        ids.append(f"DOI:{doi}")
    if arxiv_id:
        ids.append(f"arXiv:{arxiv_id}")
    elif doi and ARXIV_DOI.match(doi):
        ids.append(f"DOI:{doi}")
    for pid in ids:
        refs = _s2_references(pid, email=email, api_key=api_key)
        if refs:
            return refs
    return []


def _s2_references(pid: str, *, email: str, api_key: str) -> list[dict]:
    headers = api_headers(email)
    if api_key:
        headers["x-api-key"] = api_key
    out, offset = [], 0
    while True:
        r = _get(
            f"{S2_API}/paper/{_q(pid)}/references",
            headers=headers,
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


def _openalex_work(w: dict) -> dict:
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


def openalex(doi: str | None, *, email: str = "", api_key: str = "") -> list[dict]:
    if not doi:
        return []
    params = {"select": "id,referenced_works"}
    if api_key:
        params["api_key"] = api_key
    elif email:
        params["mailto"] = email
    r = _get(f"{OPENALEX_API}/works/doi:{_q(doi)}", params=params, headers=api_headers(email))
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
        if api_key:
            p["api_key"] = api_key
        elif email:
            p["mailto"] = email
        rr = _get(f"{OPENALEX_API}/works", params=p, headers=api_headers(email))
        for w in (rr.json().get("results") or []) if rr is not None else []:
            details[(w.get("id") or "").rsplit("/", 1)[-1]] = _openalex_work(w)
    return [details.get(w) or {"openalex_id": w} for w in wids]  # keep the original order


def crossref(doi: str | None, *, email: str = "") -> list[dict]:
    if not doi or ARXIV_DOI.match(doi):
        return []
    r = _get(f"{CROSSREF_API}/works/{_q(doi)}", headers=api_headers(email))
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


def fetch(item: dict, *, email: str = "", openalex_key: str = "", s2_key: str = "") -> tuple[str, list[dict]]:
    """The reference list of ``item``: (source name, references). Raises LookupFailed when the
    item has no DOI or arXiv id; returns ("none", []) when no source knows its references."""
    doi, arx = identifiers(item)
    if not doi and not arx:
        raise LookupFailed("This item has no DOI or arXiv ID to look up its references with.")
    attempts = [
        ("semanticscholar", lambda: semantic_scholar(doi, arx, email=email, api_key=s2_key)),
        (
            "openalex",
            lambda: openalex(doi or (f"10.48550/arxiv.{arx}" if arx else None), email=email, api_key=openalex_key),
        ),
        ("crossref", lambda: crossref(doi, email=email)),
    ]
    if arx:
        # arXiv's HTML version lists the references from the day a paper is announced, before the
        # other services know them
        attempts.append(("arxiv-html", lambda: arxiv_html.fetch(arx)))
    errors = []
    for name, run in attempts:
        try:
            refs = [_clean_ref(r) for r in run()]
        except Exception as e:  # a failing service must not hide the next one
            log.info("References from %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
            continue
        refs = [r for r in refs if any(r[k] for k in ("doi", "arxiv_id", "openalex_id", "s2_id", "title", "raw"))]
        if refs:
            return name, refs
    if errors:  # an answer of "no references" only counts when every service answered
        raise FetchFailed("Could not get the references (" + "; ".join(errors) + ").")
    return "none", []
