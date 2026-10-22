"""``plugin.services`` and ``plugin.actions``: the core's own machinery, for plugins.

Services read (looking up a paper by identifier, its reference list, matching references to the
library, how papers connect to it); actions change the library through the same code as the app
(adding a paper: the duplicate check, a citation key, the change feed), and need ``"library":
["add"]`` in the manifest. Both reach the web the way Synapse itself does (the scholarly services
it already uses), not through ``plugin.http``.
"""

from __future__ import annotations

import requests

from ..library.identifiers import norm_arxiv
from ..online.http import LookupFailed
from .errors import PluginError
from .http import Offline
from .outside import record


class PluginServices:
    """``plugin.services``."""

    def __init__(self, services):
        self._s = services

    def contact_email(self) -> str:
        """The contact address from Settings → Online Services ('' when not set): scholarly
        services ask for one (Unpaywall requires it)."""
        return str(self._s.settings.get("contact_email") or "")

    def lookup(self, identifier: str) -> dict:
        """A paper's metadata by DOI, arXiv id (``arXiv:2610.01234``) or ISBN, as an outside record
        (not added to the library)."""
        from ..online.metadata import known_authors, lookup

        try:
            meta = lookup(
                identifier=identifier,
                known=known_authors(self._s.papers.list()),
                email=self._s.settings.get("contact_email"),
            )
        except LookupFailed as e:
            raise PluginError(str(e)) from e
        except requests.RequestException as e:
            raise Offline(f"The lookup of {identifier} could not reach the web: {e}") from e
        arx = norm_arxiv(meta.get("archive_id")) or norm_arxiv(identifier if "arxiv" in identifier.lower() else None)
        return record({**meta, "arxiv": arx})

    def references(self, paper: dict, *, new: bool = False) -> list[dict]:
        """The works ``paper`` cites (each with ``doi``, ``arxiv_id``, ``title``, ``year``, ``raw``
        when known): from Semantic Scholar, OpenAlex or Crossref, else from arXiv's HTML version,
        which has them from the day a paper is announced (and is preferred for recent arXiv
        papers when it lists more). ``new``: a paper announced in the last days, whose HTML
        version is asked first (the others rarely know it yet). ``[]`` when none is known;
        ``Offline`` when the services cannot be reached (try again later)."""
        from ..references.sources import FetchFailed, fetch

        item = {"doi": paper.get("doi"), "archive_id": f"arXiv:{paper['arxiv']}" if paper.get("arxiv") else None}
        s = self._s.settings
        try:
            _, refs = fetch(
                item,
                email=s.get("contact_email"),
                openalex_key=s.get("openalex_api_key"),
                s2_key=s.get("semantic_scholar_api_key"),
                fresh=new,
            )
        except FetchFailed as e:
            raise Offline(str(e)) from e
        except LookupFailed as e:
            raise PluginError(str(e)) from e
        return refs

    def match(self, refs: list[dict]) -> list[str | None]:
        """For each reference, the library item it is (by DOI, arXiv id or title and year), or None."""
        return self._s.citations.match_refs(refs)

    def connections(self, papers: list[dict], refs: dict | None = None, folders: list[int] | None = None) -> list[dict]:
        """How each paper connects to the library: ``{"score", "reasons": [{kind, text, items}]}``,
        from the library papers it cites (``refs``: ``{key: references}``), those citing it,
        shared authors and similar titles and abstracts. ``folders``: only those folders count."""
        recs = [p if p.get("key") else {**p, "key": None} for p in papers]
        return self._s.connections.score(recs, refs, set(folders) if folders else None)


class PluginActions:
    """``plugin.actions``: changes to the library, as the app makes them."""

    def __init__(self, services, manifest):
        self._s = services
        self._man = manifest

    def add(self, paper: dict, *, folder_id: int | None = None, tags: list[str] | None = None) -> dict:
        """Add an outside paper (a record, or a dict with ``doi`` / ``arxiv``) to the library, as the
        "+" in the app does. Returns the library item, with ``"duplicate": True`` when the paper was
        already there."""
        if "add" not in self._man.library:
            raise PluginError(f'{self._man.name} needs "library": ["add"] in its manifest to add papers')
        rec = paper if paper.get("_outside") else record(paper)
        item, dup = self._s.add_paper(rec, folder_id=folder_id, tags=tags or [])
        if not dup:
            # what Settings → Adding Items says to do always (nobody can be asked here)
            self._s.citations.maybe_enqueue(item, None)
            if self._s.settings.get("add_find_pdf") == "always":
                self._s.find_pdf_later(item["id"])
        return {**item, "duplicate": dup}
