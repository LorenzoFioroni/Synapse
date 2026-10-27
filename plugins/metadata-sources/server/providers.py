"""Crossref, OpenAlex, arXiv and Open Library through the public plugin HTTP client."""

from __future__ import annotations

import logging
import re
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import CancelledError, ThreadPoolExecutor
from urllib.parse import quote

from backend.plugins import HttpError, PluginError, scholarly

from .parsing import ARXIV_NS, arxiv_item, crossref_to_item, openlibrary_to_item

ARXIV_API = "https://export.arxiv.org/api/query"
TITLE_MATCH = scholarly.TITLE_MATCH
clean_doi = scholarly.clean_doi
join_authors = scholarly.join_authors
parse_display_name = scholarly.parse_display_name
title_similarity = scholarly.title_similarity
ARXIV_ID = scholarly.ARXIV_ID
log = logging.getLogger(__name__)


class LookupFailed(PluginError):
    """A readable provider lookup failure, independent of Synapse's internal exception types."""


class Providers:
    def __init__(self, http):
        self.http = http
        self._lock = threading.Lock()
        self._closed = False
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="metadata-sources")

    def close(self) -> None:
        with self._lock:
            self._closed = True
        # In-flight HTTP has the host's bounded timeout; queued work must never start after stop.
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _ensure_open(self) -> None:
        if self._closed:
            raise LookupFailed("Metadata Sources was stopped.")

    def _get(self, url: str, **kwargs):
        with self._lock:
            self._ensure_open()
        return self.http.get(url, **kwargs)

    @staticmethod
    def _json(response) -> dict:
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def crossref_doi(self, doi: str) -> dict:
        try:
            r = self._get(f"https://api.crossref.org/works/{quote(doi)}")
        except HttpError as e:
            if e.status == 404:
                raise LookupFailed(f"DOI {doi} was not found on Crossref.") from e
            raise
        return crossref_to_item(self._json(r)["message"])

    def crossref_title(self, title: str) -> dict:
        r = self._get(
            "https://api.crossref.org/works",
            params={"query.bibliographic": title, "rows": "3"},
        )
        items = self._json(r)["message"].get("items") or []
        if not items:
            raise LookupFailed("No match found on Crossref for this title.")
        # Pick the closest title rather than blindly the first hit.
        best = max(items, key=lambda it: title_similarity(title, (it.get("title") or [""])[0]))
        if title_similarity(title, (best.get("title") or [""])[0]) < TITLE_MATCH:
            raise LookupFailed("Crossref returned results, but none matched the title closely.")
        return crossref_to_item(best)

    def openalex_authors(self, doi: str) -> list[tuple[str, str]]:
        try:
            r = self._get(
                f"https://api.openalex.org/works/doi:{quote(doi)}",
            )
            # Profile names can be wrong; the raw name is specific to this work.
            return [
                parse_display_name(
                    (a.get("raw_author_name") or "").strip() or (a.get("author") or {}).get("display_name") or ""
                )
                for a in self._json(r).get("authorships") or []
            ]
        except Exception as e:
            log.info("OpenAlex failed: %s", e)
            return []

    def _arxiv_entries(self, params: dict) -> list:
        r = self._get(ARXIV_API, params=params)
        return [
            e
            for e in ET.fromstring(r.content).findall("a:entry", ARXIV_NS)
            if e.find("a:title", ARXIV_NS) is not None
            and (e.findtext("a:id", default="", namespaces=ARXIV_NS) or "").find("api/errors") < 0
        ]

    def arxiv_title(self, title: str) -> dict:
        """Search arXiv by title (for preprints that Crossref does not know)."""
        words = re.findall(r"[A-Za-z0-9]+", title or "")
        if not words:
            raise LookupFailed("No title to search for.")
        entries = self._arxiv_entries({"search_query": "ti:(" + " AND ".join(words[:12]) + ")", "max_results": 5})
        scored = [(title_similarity(title, e.findtext("a:title", default="", namespaces=ARXIV_NS)), e) for e in entries]
        scored = [x for x in scored if x[0] >= TITLE_MATCH]
        if not scored:
            raise LookupFailed("No close match on Crossref or arXiv for this title.")
        entry = max(scored, key=lambda x: x[0])[1]
        m = ARXIV_ID.search(entry.findtext("a:id", default="", namespaces=ARXIV_NS).split("/abs/")[-1])
        return arxiv_item(entry, m.group(1) if m else "")

    def arxiv(self, arxiv_id: str) -> dict:
        entries = self._arxiv_entries({"id_list": arxiv_id})
        if not entries:
            raise LookupFailed(f"arXiv:{arxiv_id} was not found.")
        return arxiv_item(entries[0], arxiv_id)

    def openlibrary_isbn(self, isbn: str) -> dict:
        r = self._get(
            "https://openlibrary.org/api/books",
            params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
        )
        d = self._json(r).get(f"ISBN:{isbn}")
        if not d:
            raise LookupFailed(f"ISBN {isbn} was not found on Open Library.")
        return openlibrary_to_item(d, isbn)

    def resolve(self, query: dict) -> dict:
        kind, value = query["kind"], query["value"]
        # With a DOI known up front, ask OpenAlex for the authors at the same time as Crossref.
        with self._lock:
            self._ensure_open()
            oa_future = self._pool.submit(self.openalex_authors, value) if kind == "doi" else None
        try:
            if kind == "doi":
                item = self.crossref_doi(value)
            elif kind == "arxiv":
                item = self.arxiv(value)
            elif kind == "isbn":
                item = self.openlibrary_isbn(value)
            elif kind == "title":
                try:
                    item = self.crossref_title(value)
                except LookupFailed as crossref_error:
                    try:
                        item = self.arxiv_title(value)
                    except LookupFailed:
                        raise LookupFailed(
                            "No close match for this title on Crossref or arXiv. "
                            "Add the DOI or arXiv ID to the item and try again."
                        ) from None
                    except (HttpError, ET.ParseError):
                        raise crossref_error from None
            else:
                raise LookupFailed("PubMed lookups are not supported yet.")

            if item.get("doi") and kind in ("doi", "title"):
                if oa_future is not None and clean_doi(item["doi"]).lower() == value.lower():
                    oa = oa_future.result()
                else:
                    oa = self.openalex_authors(item["doi"])
                # OpenAlex's author list wins when it is more complete than Crossref's.
                if oa and len(join_authors(oa)) > len(item.get("authors") or ""):
                    item["authors"] = join_authors(oa)
            self._ensure_open()
            return item
        except CancelledError as e:
            raise LookupFailed("Metadata Sources was stopped.") from e
        except HttpError as e:
            raise LookupFailed(f"Could not retrieve metadata: {e}") from e
        except (ValueError, TypeError, KeyError, AttributeError, ET.ParseError) as e:
            raise LookupFailed("The metadata service returned invalid data. Try again later.") from e
        finally:
            if oa_future is not None:
                oa_future.cancel()
