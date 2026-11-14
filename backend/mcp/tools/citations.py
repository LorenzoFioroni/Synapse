"""Tools for the citation graph: get_references, get_citation_graph."""

from __future__ import annotations

import re
import unicodedata

from .common import ToolsBase, drop_empty, short_authors

CITERS_LISTED = 10  # citing keys named per work; the count is always given
MANY_RECORDS = 6  # records with one title: a work has a few ids (preprint, journal, book); dozens is a mismatch


def _title_key(title: str | None) -> str:
    """A title as one run of letters and digits, without a leading "the": reference lists spell the same
    book differently (case, punctuation, "The"). Short titles are too generic to compare ("")."""
    t = unicodedata.normalize("NFKD", str(title or "")).casefold()
    t = re.sub(r"^the\s+", "", re.sub(r"[^\w\s]", "", "".join(c for c in t if not unicodedata.combining(c))).strip())
    t = re.sub(r"\s+", " ", t)
    return t if len(t) >= 12 else ""


class CitationToolsMixin(ToolsBase):
    """get_references, get_citation_graph."""

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

    def get_references(self, paper: str, direction: str = "cites", in_library_only: bool = False, limit: int = 200):
        items = self._items()
        p = self._paper(paper, items)
        view = self.s.citations.item_view(p["id"])
        out: dict = {"paper": self._label(p), "title": p.get("title")}
        f = view["fetch"]
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
            refs = []
            for r in view["references"]:
                lib = items.get(r.get("library_id")) if r.get("library_id") else None
                if lib:
                    refs.append({"in_library": True, **self._brief(lib)})
                elif not in_library_only:
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
            out["references"] = refs[:limit]
            if len(refs) > limit:
                out["references_cut"] = f"showing {limit} of {len(refs)}"
        if direction in ("cited_by", "both"):
            out["cited_by_in_library"] = [self._brief(items[c["id"]]) for c in view["cited_by"] if c["id"] in items]
            out["cited_by_note"] = (
                "Only items of the library are known to cite it (works outside the library are not tracked)."
            )
        return out

    def get_citation_graph(
        self, papers: list | None = None, depth: int = 1, limit: int = 40, include_edges: bool = False
    ):
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
                    "arxiv": n.get("arxiv_id"),
                }
            )

        citers: dict[str, list[str]] = {}
        for e in g["edges"]:
            citers.setdefault(e["target"], []).append(e["source"])

        if not papers:
            in_lib = {
                k: self._label(items[n["item_id"]])
                for n in nodes.values()
                if n["in_library"] and n["item_id"] in items and (k := _title_key(n.get("title")))
            }
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
            # Many records with one title, or none with a DOI or arXiv id, are what a wrong automatic match
            # looks like: they come after the works that are identified, and say so.
            weak = lambda ns: len(ns) >= MANY_RECORDS or not any(n.get("doi") or n.get("arxiv_id") for n in ns)
            rows.sort(key=lambda r: (weak(r[2]), -r[0]))
            cited_lib = sorted(
                (n for n in nodes.values() if n["in_library"] and n["cited_by_library"]),
                key=lambda n: -n["cited_by_library"],
            )
            return {
                "stats": g["stats"],
                "most_cited_not_in_library": [
                    drop_empty(
                        {
                            **describe(best["id"]),
                            "cited_by_count": count,
                            "cited_by": cs[:CITERS_LISTED],
                            "same_title_in_library": mine,  # another edition, or the reference list matched the wrong work
                            "check": "matched from reference text only (no DOI or arXiv id, or very many records "
                            "share this title): may be a wrong match"
                            if weak(ns)
                            else None,
                            "also_listed_as": [describe(n["id"]) for n in ns if n is not best][:2] or None,
                            "also_listed_as_count": len(ns) - 1 if len(ns) > 3 else None,
                        }
                    )
                    for count, best, ns, cs, mine in rows[:limit]
                ],
                "most_cited_in_library": [
                    {**describe(n["id"]), "cited_by_library": n["cited_by_library"]} for n in cited_lib[:limit]
                ],
                "note": "Only items whose reference lists were fetched contribute edges. Reference lists are "
                "matched automatically: check a surprising entry (same_title_in_library) with get_references.",
            }

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
        return drop_empty(
            {
                "seeds": [label(s) for s in seeds],
                "depth": depth,
                "nodes": [{**describe(n), "node": label(n), "distance": dist[n]} for n in keep],
                "edges": [f"{label(a)} -> {label(b)}" for a, b in shown][:1000] if include_edges else None,
                "edges_meaning": "a -> b: a cites b" if include_edges else None,
                "edges_total": len(edges),
                "cited_by_several": [
                    {
                        "node": label(n),
                        "title": nodes[n].get("title"),
                        "in_library": nodes[n]["in_library"],
                        "cited_by_count": len(cs),
                        "cited_by": sorted(label(c) for c in cs)[:10],
                    }
                    for n, cs in shared[:limit]
                ],
                "truncated": (
                    f"showing {len(keep)} of {len(dist)} nodes (nearest first; raise limit for more)"
                    if len(dist) > limit
                    else None
                ),
                "seeds_without_reference_list": no_refs or None,
            }
        )
