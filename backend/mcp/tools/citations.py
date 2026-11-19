"""Tools for the citation graph: get_references, get_citation_graph, get_citing_works."""

from __future__ import annotations

import json
import re
import unicodedata

from ...library.identifiers import ARXIV_DOI, identifiers, norm_arxiv, norm_doi
from ...references.sources import LookupFailed
from ..protocol import ToolError
from .common import (
    _ARXIV_REF,
    _DOI_REF,
    MAX_LISTED_CHARS,
    ToolsBase,
    drop_empty,
    first_author,
    sentence,
    short_authors,
)

CITERS_LISTED = 10  # citing keys named per work; the count is always given
MANY_RECORDS = 6  # records with one title: a work has a few ids (preprint, journal, book); dozens is a mismatch
CITERS_COMPACT = 5
LATE_ARXIV_YEARS = 10  # an arXiv id this many years after the work is a record of another paper
SHORT_TITLE_WORDS = (
    3  # titles this short ("Quantum Optics") are shared by many works; sources match books to the wrong one
)
SAME_FIRST_AUTHOR = 0.8  # the share of citations that names the first author of the records sharing a title
AUTO_COMPACT = 40  # more works than this come one per line unless the caller asks for their fields


def _outside_line(title, authors, year, doi, arxiv) -> str:
    """A work that is not in the library on one line, with the id to look it up by."""
    ident = f"doi:{doi}" if doi else f"arXiv:{arxiv}" if arxiv else "no DOI or arXiv id"
    who = first_author(authors)
    head = f"{who} ({year or 'n.d.'})" if who else f"({year})" if year else ""
    return f"{head}{': ' if head else ''}{' '.join(str(title or '?').split())} [{ident}]"


def _arxiv_shown(arxiv, year):
    """The arXiv id, unless it was assigned years after the work appeared: sources attach the id of another
    paper to a work they matched wrongly (a 2002 paper with an id of 2025)."""
    m = re.match(r"(\d\d)\d\d\.\d{4,5}", str(arxiv or ""))
    try:
        late = m is not None and 2000 + int(m[1]) - int(year) >= LATE_ARXIV_YEARS
    except (TypeError, ValueError):
        late = False
    return None if late else arxiv


def _surname(authors) -> str:
    name = first_author(authors).removesuffix(" et al.")
    return re.sub(
        r"\W+", "", "".join(c for c in unicodedata.normalize("NFKD", name.casefold()) if not unicodedata.combining(c))
    )


def _unread(r: dict) -> bool:
    """A reference with no author, year or identifier: a footnote, or text the source took for a reference."""
    return not (r.get("authors") or r.get("year") or r.get("doi") or r.get("arxiv_id"))


def _title_key(title: str | None) -> str:
    """A title as one run of letters and digits, without a leading "the": reference lists spell the same
    book differently (case, punctuation, "The"). Short titles are too generic to compare ("")."""
    t = unicodedata.normalize("NFKD", str(title or "")).casefold()
    t = re.sub(r"^the\s+", "", re.sub(r"[^\w\s]", "", "".join(c for c in t if not unicodedata.combining(c))).strip())
    t = re.sub(r"\s+", " ", t)
    return t if len(t) >= 12 else ""


class CitationToolsMixin(ToolsBase):
    """get_references, get_citation_graph, get_citing_works."""

    @staticmethod
    def _merge_subtitled(groups: dict[str, list[dict]], citers: dict[str, list[str]], label) -> None:
        """A work cited by its title alone ("Exploring the Quantum") and with its subtitle ("… Atoms, Cavities,
        and Photons"): the short title absorbs the longer ones. Only popular short titles of three words or
        more take part, so that two different works with a common beginning stay apart."""

        def popular(ns):
            return len({label(c) for n in ns for c in citers.get(n["id"], [])}) >= 5

        for k in sorted((k for k, ns in groups.items() if len(k.split()) >= 3 and popular(ns)), key=len):
            if k not in groups:
                continue
            for other in [o for o in groups if o != k and o.startswith(k + " ")]:
                groups[k] += groups.pop(other)

    def get_references(
        self,
        paper: str,
        direction: str = "cites",
        in_library_only: bool = False,
        limit: int = 200,
        compact: bool | None = None,
        offset: int = 0,
    ):
        items = self._items()
        p = self._paper(paper, items)
        view = self.s.citations.item_view(p["id"])
        out: dict = {"paper": self._label(p), "title": p.get("title")}
        f = view["fetch"]
        asked = compact
        if compact is None:  # a long list in full is more than an AI app accepts in one result
            cites = direction in ("cites", "both")
            listed = sum(1 for r in view["references"] if r.get("library_id") or not (in_library_only or _unread(r)))
            compact = min(listed, limit) * cites + len(view["cited_by"]) * (direction != "cites") > AUTO_COMPACT
        if direction in ("cites", "both"):
            if not f:
                out["references_note"] = (
                    "Its reference list was never fetched. "
                    "In the app: item menu → Fetch References (needs a DOI or arXiv id)."
                    if view["can_fetch"]
                    else "No reference list: the item has no DOI or arXiv id to look it up."
                )
            elif f.get("error") and not view["references"]:
                out["references_note"] = f"Fetching the reference list failed: {f['error']}"
            refs: list = []
            unread = 0
            for r in view["references"]:
                lib = items.get(r.get("library_id")) if r.get("library_id") else None
                if lib:
                    refs.append(
                        f"in library: {self._one_line(lib)}" if compact else {"in_library": True, **self._brief(lib)}
                    )
                elif in_library_only:
                    continue
                elif _unread(r):
                    unread += 1
                elif compact:
                    refs.append(
                        _outside_line(
                            r.get("title") or r.get("raw"),
                            r.get("authors"),
                            r.get("year"),
                            r.get("doi"),
                            r.get("arxiv_id"),
                        )
                    )
                else:
                    refs.append(
                        drop_empty(
                            {
                                "in_library": False,
                                "title": r.get("title") or r.get("raw"),
                                "authors": short_authors(r.get("authors")),
                                "year": r.get("year"),
                                "venue": r.get("venue"),
                                "doi": r.get("doi"),
                                "arxiv": r.get("arxiv_id"),
                            }
                        )
                    )
            out["references_total"] = len(view["references"])
            out["references_in_library"] = sum(1 for r in view["references"] if r.get("library_id"))
            if f:
                out["source"] = f.get("source")
            shown: list = []
            size = 0
            for r in refs[offset : offset + limit]:
                size += len(r) if isinstance(r, str) else len(json.dumps(r, ensure_ascii=False))
                if shown and size > MAX_LISTED_CHARS:  # more than an AI app accepts in one result
                    break
                shown.append(r)
            if offset or len(shown) < len(refs):  # said before the list, where it is seen
                end = offset + len(shown)
                out["references_cut"] = f"showing {offset + 1}-{end} of {len(refs)}" + (
                    f"; offset={end} lists the next ones" if end < len(refs) else ""
                )
            out["references"] = shown
            if unread:
                out["not_listed"] = (
                    f"{unread} entries with no author, year, DOI or arXiv id (footnotes, or text the source "
                    "took for a reference)"
                )
            if not compact and len(out["references"]) > AUTO_COMPACT:
                out["hint"] = "compact=true gives one line per reference."
        if direction in ("cited_by", "both"):
            out["cited_by_in_library"] = [
                self._one_line(items[c["id"]]) if compact else self._brief(items[c["id"]])
                for c in view["cited_by"]
                if c["id"] in items
            ]
            out["cited_by_note"] = (
                "Only items of the library are known to cite it (works outside the library are not tracked)."
            )
        if compact and asked is None:
            out["compact"] = (
                "one line per work, as the list is long; compact=false gives each work's fields "
                "(co-authors, venue, both ids)"
            )
        return out

    def get_citing_works(
        self,
        paper: str,
        limit: int = 50,
        offset: int = 0,
        sort: str = "cited",
        since_year: int | None = None,
        compact: bool | None = None,
    ):
        """The works citing a paper, asked online of the citation sources (OpenAlex, Semantic Scholar): unlike
        the stored graph, it knows citing works that are not in the library."""
        items = self._items()
        raw = str(paper or "").strip()
        p = self._by_identifier(raw, items)
        if p is None and (_DOI_REF.match(raw) or _ARXIV_REF.match(raw)):  # a work outside the library
            doi = norm_doi(raw) if _DOI_REF.match(raw) else None
            arx = norm_arxiv(re.sub(r"\.pdf$", "", raw, flags=re.I)) if _ARXIV_REF.match(raw) else None
            if doi and (m := ARXIV_DOI.match(doi)):
                arx = norm_arxiv(m.group(1))
            name = f"doi:{doi}" if doi else f"arXiv:{arx}"
        else:
            p = p or self._paper(raw, items)
            doi, arx = identifiers(p)
            name = self._label(p)
        try:
            found = self.s.citations.citing_works(doi, arx, limit=offset + limit, sort=sort, since_year=since_year)
        except LookupFailed as e:
            raise ToolError(f"The works citing '{name}' could not be looked up: {sentence(str(e))}") from e
        except Exception as e:  # network trouble: its text can hold request URLs
            raise ToolError(
                f"Looking up the works citing '{name}' failed ({type(e).__name__}). Try again later."
            ) from e
        works = found["works"]
        by_id = {p["id"]: p for p in items.values()}
        listed = works[offset : offset + limit]
        asked = compact
        if compact is None:
            compact = len(listed) > AUTO_COMPACT
        rows: list = []
        size = 0
        for w in listed:
            lib = by_id.get(w.get("library_id")) if w.get("library_id") else None
            count = w.get("cited_by_count")
            if compact:
                times = f" [cited {count}x]" if count is not None else ""
                row: str | dict = (
                    f"in library: {self._one_line(lib)}{times}"
                    if lib
                    else _outside_line(w["title"], w["authors"], w["year"], w["doi"], w["arxiv_id"]) + times
                )
            elif lib:
                row = drop_empty({"in_library": True, **self._brief(lib), "cited_by_count": count})
            else:
                row = drop_empty(
                    {
                        "in_library": False,
                        "title": w["title"],
                        "authors": short_authors(w["authors"]),
                        "year": w["year"],
                        "venue": w["venue"],
                        "doi": w["doi"],
                        "arxiv": w["arxiv_id"],
                        "cited_by_count": count,
                    }
                )
            size += len(row) if isinstance(row, str) else len(json.dumps(row, ensure_ascii=False))
            if rows and size > MAX_LISTED_CHARS:  # more than an AI app accepts in one result
                break
            rows.append(row)
        end = offset + len(rows)
        more = len(works) > end or (found["total"] or 0) > end
        return drop_empty(
            {
                "paper": name,
                "title": p.get("title") if p else None,
                "source": found["source"],
                "citing_total": found["total"],
                "order": "most cited first" if sort != "recent" else "newest first",
                "since_year": since_year,
                "shown": f"{offset + 1}-{end}" + (f"; offset={end} lists the next ones" if more else "")
                if rows
                else None,
                "in_library_shown": sum(1 for w in listed[: len(rows)] if w.get("library_id")),
                "works": rows,
                "note": None
                if works or offset
                else "The source knows no work citing it (a very new paper, or one it has under another record).",
                "source_note": found.get("note"),
                "about": "Looked up online now, not stored. Services count differently, and a preprint and its "
                "journal version may be counted apart."
                + (
                    " One line per work, as the list is long: compact=false gives the fields."
                    if compact and asked is None
                    else ""
                ),
            }
        )

    def get_citation_graph(
        self,
        papers: list | None = None,
        depth: int = 1,
        limit: int = 40,
        include_edges: bool = False,
        compact: bool | None = None,
    ):
        asked = compact
        g = self.s.citations.graph()
        items = {p["id"]: p for p in self.s.papers.list()}
        nodes = {n["id"]: n for n in g["nodes"]}

        def label(nid: str) -> str:
            n = nodes[nid]
            return self._label(items[n["item_id"]]) if n["in_library"] and n["item_id"] in items else nid

        def describe(nid: str) -> dict:
            n = nodes[nid]
            if n["in_library"] and n["item_id"] in items:
                return self._brief(items[n["item_id"]])
            return drop_empty(
                {
                    "id": nid,
                    "title": n.get("title"),
                    "authors": short_authors(n.get("authors")),
                    "year": n.get("year"),
                    "doi": n.get("doi"),
                    "arxiv": _arxiv_shown(n.get("arxiv_id"), n.get("year")),
                }
            )

        def line(nid: str) -> str:
            n = nodes[nid]
            if n["in_library"] and n["item_id"] in items:
                return self._one_line(items[n["item_id"]])
            arxiv = _arxiv_shown(n.get("arxiv_id"), n.get("year"))
            return _outside_line(n.get("title"), n.get("authors"), n.get("year"), n.get("doi"), arxiv)

        in_lib = {
            k: self._label(items[n["item_id"]])
            for n in nodes.values()
            if n["in_library"] and n["item_id"] in items and (k := _title_key(n.get("title")))
        }

        citers: dict[str, list[str]] = {}
        for e in g["edges"]:
            citers.setdefault(e["target"], []).append(e["source"])

        if not papers:
            # The same work often appears under several ids (editions, a book and its review): group them.
            groups: dict[str, list[dict]] = {}
            for n in nodes.values():
                if not n["in_library"] and n["cited_by_library"] >= 1 and len(n.get("title") or "") >= 10:
                    groups.setdefault(_title_key(n.get("title")) or n["id"], []).append(n)
            self._merge_subtitled(groups, citers, label)
            rows = []
            for k, ns in groups.items():
                cs = sorted({label(c) for n in ns for c in citers.get(n["id"], [])})
                if len(cs) >= 2:
                    best = max(ns, key=lambda n: n["cited_by_library"])
                    rows.append((len(cs), best, ns, cs, in_lib.get(k)))

            def firsts(ns) -> dict[str, int]:
                """First authors the records of one title name, with the citations each has; empty when one of
                them has nearly all (the records are one work) or only one is named."""
                shown: dict[str, str] = {}
                names: dict[str, int] = {}
                for n in ns:
                    if who := _surname(n.get("authors")):
                        shown.setdefault(who, first_author(n["authors"]).removesuffix(" et al."))
                        names[who] = names.get(who, 0) + n["cited_by_library"]
                if len(names) < 2 or max(names.values()) >= SAME_FIRST_AUTHOR * sum(names.values()):
                    return {}
                return {shown[w]: c for w, c in sorted(names.items(), key=lambda x: -x[1])}

            # Many records with one title, or none with a DOI or arXiv id, or records naming different first
            # authors, are what a wrong automatic match looks like: they come after the works that are
            # identified, and say so.
            weak = lambda ns: (
                len(ns) >= MANY_RECORDS or not any(n.get("doi") or n.get("arxiv_id") for n in ns) or bool(firsts(ns))
            )
            rows.sort(key=lambda r: (weak(r[2]), -r[0]))
            # A "missing" work whose title a library item has is that item under another record (an edition, or
            # a reference the source matched to the wrong work): it is listed apart, not as missing.
            missing = [r for r in rows if not r[4]]
            mine = [r for r in rows if r[4]]
            cited_lib = sorted(
                (n for n in nodes.values() if n["in_library"] and n["cited_by_library"]),
                key=lambda n: -n["cited_by_library"],
            )
            if compact is None:  # two ranked lists of up to `limit` works each
                compact = len(missing[:limit]) + len(cited_lib[:limit]) > AUTO_COMPACT
            check = (
                "matched from reference text only (no DOI or arXiv id, or very many records share this title): "
                "may be a wrong match"
            )
            mixed = (
                "the records with this title name different first authors (see records): another work with the "
                "same title, or a reference matched to the wrong record; the first one listed may not be the work"
            )
            short = (
                "a title of three words or fewer: several works have it, and a book is often matched to another "
                "work of that name"
            )
            by_citations = lambda ns: sorted(ns, key=lambda n: -n["cited_by_library"])
            is_short = lambda best: len(str(best.get("title") or "").split()) <= SHORT_TITLE_WORDS

            def doubt(ns, best) -> str:
                if who := firsts(ns):
                    return f" (check: first authors differ: {', '.join(f'{w} {c}x' for w, c in who.items())})"
                if weak(ns):
                    return " (check: may be a wrong match)"
                return " (check: short title, may be another work of that name)" if is_short(best) else ""

            if compact:
                not_in_library: list = [
                    f"{count}x {line(best['id'])}{doubt(ns, best)} "
                    f"— cited by {', '.join(cs[:CITERS_COMPACT])}{', …' if len(cs) > CITERS_COMPACT else ''}"
                    for count, best, ns, cs, _ in missing[:limit]
                ]
                in_library: list = [f"{n['cited_by_library']}x {line(n['id'])}" for n in cited_lib[:limit]]
            else:
                not_in_library = [
                    drop_empty(
                        {
                            **describe(best["id"]),
                            "cited_by_count": count,
                            "cited_by": cs[:CITERS_LISTED],
                            "check": mixed if firsts(ns) else check if weak(ns) else short if is_short(best) else None,
                            "records": [f"{n['cited_by_library']}x {line(n['id'])}" for n in by_citations(ns)[:4]]
                            if firsts(ns)
                            else None,
                            "also_listed_as": None
                            if firsts(ns)
                            else [describe(n["id"]) for n in ns if n is not best][:2] or None,
                            "also_listed_as_count": len(ns) - 1 if len(ns) > 3 and not firsts(ns) else None,
                        }
                    )
                    for count, best, ns, cs, _ in missing[:limit]
                ]
                in_library = [
                    {**describe(n["id"]), "cited_by_library": n["cited_by_library"]} for n in cited_lib[:limit]
                ]
            return drop_empty(
                {
                    "stats": g["stats"],
                    "most_cited_not_in_library": not_in_library,
                    "in_library_under_another_record": [
                        f"{count}x {' '.join(str(best.get('title') or '').split())} = {key} (same title: another "
                        "edition, or a reference matched to the wrong work)"
                        for count, best, _, _, key in mine[:limit]
                    ],
                    "most_cited_in_library": in_library,
                    "note": "Only items whose reference lists were fetched contribute edges. Reference lists are "
                    "matched automatically: check a surprising entry with get_references."
                    + ("" if compact else " compact=true gives one line per work.")
                    + (
                        " One line per work, as the lists are long: compact=false gives their fields."
                        if compact and asked is None
                        else ""
                    ),
                }
            )

        all_items = self._items()
        seeds = []
        for ref in papers:
            p = self._paper(ref, all_items)
            seeds.append(f"item:{p['id']}")
        adj: dict[str, set[str]] = {}
        for e in g["edges"]:
            adj.setdefault(e["source"], set()).add(e["target"])
            adj.setdefault(e["target"], set()).add(e["source"])
        dist = {s: 0 for s in seeds if s in nodes}
        frontier = list(dist)
        for d in range(1, depth + 1):
            nxt = []
            for nid in frontier:
                for m in adj.get(nid, ()):
                    if m not in dist:
                        dist[m] = d
                        nxt.append(m)
            frontier = nxt
        # Shared foundations are counted over the whole neighbourhood; only the listing is cut to `limit`.
        edges = [(e["source"], e["target"]) for e in g["edges"] if e["source"] in dist and e["target"] in dist]
        degree: dict[str, int] = {}
        for a, b in edges:
            degree[a] = degree.get(a, 0) + 1
            degree[b] = degree.get(b, 0) + 1
        keep = sorted(dist, key=lambda n: (dist[n], not nodes[n]["in_library"], -degree.get(n, 0), n))[:limit]
        kept = set(keep)
        in_sub_citers: dict[str, set[str]] = {}
        for a, b in edges:
            in_sub_citers.setdefault(b, set()).add(a)
        shared = sorted(
            ((n, c) for n, c in in_sub_citers.items() if len(c) >= 2 and n not in seeds), key=lambda x: -len(x[1])
        )
        no_refs = [
            label(s)
            for s in seeds
            if s in nodes and s not in self.s.citations.store.fetched_ids() and not any(e[0] == s for e in edges)
        ]
        shown = [(a, b) for a, b in edges if a in kept and b in kept]
        if compact is None:  # the nodes and the works they share
            compact = len(keep) + len(shared[:limit]) > AUTO_COMPACT
        return drop_empty(
            {
                "seeds": [label(s) for s in seeds],
                "depth": depth,
                "nodes": [f"{dist[n]}: {line(n)}" for n in keep]
                if compact
                else [{**describe(n), "distance": dist[n]} for n in keep],
                "nodes_meaning": "distance from the seeds: the work"
                + (" (one line per work, as there are many: compact=false gives their fields)" if asked is None else "")
                if compact
                else None,
                "edges": [f"{label(a)} -> {label(b)}" for a, b in shown][:1000] if include_edges else None,
                "edges_meaning": "a -> b: a cites b" if include_edges else None,
                "edges_total": len(edges),
                "cited_by_several": [
                    f"{len(cs)}x {line(n)}" + ("" if nodes[n]["in_library"] else " (not in the library)")
                    if compact
                    else drop_empty(
                        {
                            "node": label(n),
                            "title": nodes[n].get("title"),
                            "in_library": nodes[n]["in_library"],
                            "same_title_in_library": None
                            if nodes[n]["in_library"]
                            else in_lib.get(_title_key(nodes[n].get("title"))),
                            "cited_by_count": len(cs),
                            "cited_by": sorted(label(c) for c in cs)[:10],
                        }
                    )
                    for n, cs in shared[:limit]
                ],
                "cited_by_several_meaning": "works cited by two or more papers of this neighbourhood; the count "
                "is within the neighbourhood, not the whole library",
                "truncated": (
                    f"showing {len(keep)} of {len(dist)} nodes (nearest first; raise limit for more)"
                    if len(dist) > limit
                    else None
                ),
                "seeds_without_reference_list": no_refs or None,
            }
        )
