"""How a paper outside the library connects to it: the library papers it cites, those that cite it,
shared authors, and how close its title and abstract are to the library's (TF-IDF). Each
connection is a reason the app can show ("Cites 4 of your papers"); together they give a score
to rank a day's arXiv listing, say.

The library side (authors, the text index) is built once and rebuilt only when the library
changes.
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..library.identifiers import identifiers, norm_arxiv, norm_doi
from ..library.search import normalize

WEIGHTS = {"cites": 3.0, "cited_by": 3.0, "authors": 2.0, "similar": 10.0}
MAX_AUTHOR_ITEMS = 5  # shared authors count up to this many library papers
SIMILAR_MIN = 0.15  # cosine similarity from which a library paper counts as close
COMMON = 0.25  # words in more than this share of the library say little and are left out
STOP = set(
    """the and for with from that this these those are was were been being have has had not but its our
    their which what when where who whom how why can could may might will would should into onto over
    under than then also such via using use used based new two one three show shows shown study
    studies paper work results result method methods approach approaches we here there both each
    other more most less many much some any all between within without about against among""".split()
)
WORD_RE = re.compile(r"[a-z][a-z0-9]{2,}")


def author_keys(authors: str | list | None) -> dict[str, str]:
    """``{"cirac j": "Cirac"}``: family name and first initial, normalised, to the family name as
    written. ``authors`` is "Family, Given; Family, Given" or a list of names."""
    names = authors if isinstance(authors, list) else str(authors or "").split(";")
    out: dict[str, str] = {}
    for n in names:
        n = str(n).strip()
        if not n:
            continue
        if "," in n:
            family, given = (x.strip() for x in n.split(",", 1))
        else:  # "Given Family"
            parts = n.split()
            family, given = parts[-1], " ".join(parts[:-1])
        f = normalize(family)
        g = normalize(given)[:1]
        if f:
            out[f"{f} {g}".strip()] = family
    return out


def _words(text: str) -> list[str]:
    return [w for w in WORD_RE.findall(normalize(text)) if w not in STOP]


@dataclass
class _Library:
    signature: tuple
    titles: dict[str, str] = field(default_factory=dict)
    in_folders: dict[str, set[int]] = field(default_factory=dict)
    authors: dict[str, set[str]] = field(default_factory=dict)  # author key -> item ids
    idf: dict[str, float] = field(default_factory=dict)
    postings: dict[str, list[tuple[str, float]]] = field(default_factory=dict)  # word -> (item id, weight)


class Connections:
    """Built on the citations service (reference lists, matching) and the paper repository."""

    def __init__(self, citations, papers):
        self.citations = citations
        self.papers = papers
        self._lib: _Library | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ the library side
    def _library(self) -> _Library:
        sig = self.citations.library_signature()
        with self._lock:
            if self._lib and self._lib.signature == sig:
                return self._lib
            lib = _Library(sig)
            docs: dict[str, Counter] = {}
            df: Counter = Counter()
            for p in self.papers.list():
                pid = p["id"]
                lib.titles[pid] = p.get("title") or ""
                lib.in_folders[pid] = set(p.get("folder_ids") or [])
                for k in author_keys(p.get("authors")):
                    lib.authors.setdefault(k, set()).add(pid)
                words = Counter(_words(f"{p.get('title') or ''} {p.get('abstract') or ''}"))
                if words:
                    docs[pid] = words
                    df.update(words.keys())
            n = len(docs)
            limit = max(3, int(COMMON * n)) if n >= 20 else n + 1
            lib.idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items() if c <= limit}
            postings: dict[str, list[tuple[str, float]]] = defaultdict(list)
            for pid, words in docs.items():
                vec = {w: (1 + math.log(c)) * lib.idf[w] for w, c in words.items() if w in lib.idf}
                norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
                for w, v in vec.items():
                    postings[w].append((pid, v / norm))
            lib.postings = dict(postings)
            self._lib = lib
            return lib

    def _similar(self, lib: _Library, text: str, within: set[str] | None) -> list[tuple[str, float]]:
        words = Counter(_words(text))
        vec = {w: (1 + math.log(c)) * lib.idf[w] for w, c in words.items() if w in lib.idf}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if not norm:
            return []
        scores: dict[str, float] = defaultdict(float)
        for w, v in vec.items():
            for pid, lw in lib.postings.get(w, ()):
                scores[pid] += v / norm * lw
        best = sorted(((s, pid) for pid, s in scores.items() if within is None or pid in within), reverse=True)
        return [(pid, s) for s, pid in best[:3]]

    # ------------------------------------------------------------------ scoring
    def score(
        self,
        papers: list[dict],
        refs: dict[str, list[dict]] | None = None,
        folders: set[int] | None = None,
    ) -> list[dict]:
        """For each paper (``title``, ``authors``, ``abstract``, ``doi``/``arxiv``, and optionally
        its ``key`` to find its reference list in ``refs``): ``{"score": float, "reasons": [{kind,
        text, items}]}``. ``folders``: only library papers in these folders count."""
        lib = self._library()
        within = {pid for pid, f in lib.in_folders.items() if f & folders} if folders else None
        out = []
        for p in papers:
            reasons = []
            score = 0.0
            # the library papers it cites
            cited = []
            if refs and p.get("key") in refs:
                matched = self.citations.match_refs(refs[p["key"]])
                cited = sorted({m for m in matched if m and (within is None or m in within)})
            if cited:
                reasons.append(
                    {
                        "kind": "cites",
                        "text": f"Cites {len(cited)} of your papers" if len(cited) > 1 else "Cites one of your papers",
                        "items": cited,
                    }
                )
                score += WEIGHTS["cites"] * len(cited)
            # the library papers citing it
            doi = norm_doi(p.get("doi"))
            arx = norm_arxiv(p.get("arxiv")) or identifiers(p)[1]
            citing = (
                sorted(i for i in self.citations.citing_ids(doi, arx) if within is None or i in within)
                if (doi or arx)
                else []
            )
            if citing:
                reasons.append(
                    {
                        "kind": "cited_by",
                        "text": f"Cited by {len(citing)} of your papers"
                        if len(citing) > 1
                        else "Cited by one of your papers",
                        "items": citing,
                    }
                )
                score += WEIGHTS["cited_by"] * len(citing)
            # shared authors
            shared: dict[str, set[str]] = {}
            for k, family in author_keys(p.get("authors")).items():
                ids = {i for i in lib.authors.get(k, ()) if within is None or i in within}
                if ids:
                    shared[family] = ids
            if shared:
                ids = set().union(*shared.values())
                names = ", ".join(sorted(shared)[:4]) + (" …" if len(shared) > 4 else "")
                n = len(ids)
                reasons.append(
                    {
                        "kind": "authors",
                        "text": f"Shares authors with {n} of your papers ({names})"
                        if n > 1
                        else f"Shares authors with one of your papers ({names})",
                        "items": sorted(ids),
                    }
                )
                score += WEIGHTS["authors"] * min(n, MAX_AUTHOR_ITEMS)
            # close in title and abstract
            close = [
                (pid, s)
                for pid, s in self._similar(lib, f"{p.get('title') or ''} {p.get('abstract') or ''}", within)
                if s >= SIMILAR_MIN
            ]
            if close:
                top = lib.titles.get(close[0][0]) or "one of your papers"
                reasons.append({"kind": "similar", "text": f"Close to “{top}”", "items": [pid for pid, _ in close]})
                score += WEIGHTS["similar"] * close[0][1]
            out.append({"score": round(score, 1), "reasons": reasons})
        return out
