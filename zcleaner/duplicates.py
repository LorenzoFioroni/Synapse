"""Duplicate detection with multi-key blocking (scales to large libraries).

Signals: identical DOI / arXiv id / ISBN, and fuzzy title similarity combined
with year and first-author agreement.  Guards against the classic false
positives: "Part I" vs "Part II", errata/replies/comments, different DOIs.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from itertools import combinations

from rapidfuzz import fuzz

from . import textutil as tu
from .enrich import first_last, item_ids

SPECIAL_WORDS = {"erratum", "corrigendum", "correction", "reply", "response", "comment", "comments",
                 "retraction", "retracted", "supplement", "supplementary", "addendum", "rebuttal", "editorial"}
NUM = re.compile(r"\b(\d+|[ivxlc]+)\b")
MAX_BLOCK = 250


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def _feat(it):
    d = it["data"]
    tk = tu.title_key(d.get("title", ""))
    doi, ax = item_ids(it)
    y = re.search(r"\d{4}", (it.get("meta") or {}).get("parsedDate") or d.get("date", "") or "")
    isbn = tu.normalize_isbn_field(d.get("ISBN", "")).split()
    words = set(tk.split())
    return {"key": it["key"], "type": it["itemType"], "tk": tk, "doi": doi.lower(), "ax": ax.lower(),
            "year": int(y.group(0)) if y else None, "fa": first_last(d.get("creators")),
            "isbn": [i for i in isbn if tu.isbn_valid(i)], "nums": set(NUM.findall(tk)) - {"a"},
            "special": words & SPECIAL_WORDS}


def find_duplicates(items: list[dict], threshold: int = 90, cancelled=None) -> list[dict]:
    feats = [_feat(i) for i in items if i["itemType"] not in ("note", "attachment", "annotation") and not i["deleted"]]
    by_key = {f["key"]: f for f in feats}
    pairs: dict[tuple, tuple[float, str]] = {}

    def add(a, b, score, reason):
        k = tuple(sorted((a, b)))
        if k not in pairs or pairs[k][0] < score:
            pairs[k] = (score, reason)

    # identifier-based
    for attr, label in (("doi", "same DOI"), ("ax", "same arXiv id")):
        idx = defaultdict(list)
        for f in feats:
            if f[attr]:
                idx[f[attr]].append(f["key"])
        for ks in idx.values():
            for a, b in combinations(ks[:50], 2):
                add(a, b, 100, label)
    idx = defaultdict(list)
    for f in feats:
        for i in f["isbn"]:
            idx[i].append(f["key"])
    for ks in idx.values():
        for a, b in combinations(ks[:50], 2):
            if by_key[a]["type"] == by_key[b]["type"] == "book":
                add(a, b, 98, "same ISBN")

    # title blocking
    df = Counter(w for f in feats for w in set(f["tk"].split()))
    blocks = defaultdict(list)
    for f in feats:
        toks = f["tk"].split()
        if len(f["tk"]) < 12 or not toks:
            continue
        blocks["h:" + " ".join(toks[:3])].append(f)
        blocks["t:" + " ".join(toks[-3:])].append(f)
        rare = sorted(set(toks), key=lambda w: (df[w], w))[:2]
        blocks["r:" + " ".join(sorted(rare))].append(f)
    seen = set()
    for bk, fs in blocks.items():
        if cancelled and cancelled():
            break
        if len(fs) < 2 or len(fs) > MAX_BLOCK:
            continue
        for a, b in combinations(fs, 2):
            k = tuple(sorted((a["key"], b["key"])))
            if k in seen:
                continue
            seen.add(k)
            r = _score(a, b)
            if r and r[0] >= threshold:
                add(a["key"], b["key"], *r)

    uf = _UF()
    for (a, b) in pairs:
        uf.union(a, b)
    groups = defaultdict(set)
    gpairs = defaultdict(list)
    for (a, b), v in pairs.items():
        r = uf.find(a)
        groups[r].update((a, b))
        gpairs[r].append(v)
    out = []
    for root, keys in groups.items():
        ps = gpairs[root]
        out.append({"keys": sorted(keys), "score": round(max(v[0] for v in ps), 1),
                    "reasons": sorted({v[1] for v in ps})})
    out.sort(key=lambda g: -g["score"])
    return out


def _score(a, b):
    ta, tb = a["tk"], b["tk"]
    if not ta or not tb:
        return None
    t = fuzz.ratio(ta, tb)
    if t < 80:
        return None
    if a["nums"] != b["nums"]:
        return None  # Part I / Part II, different years or versions in the title
    if a["special"] != b["special"]:
        return None  # erratum/reply/comment vs the paper itself
    reason = f"similar titles ({t:.0f}%)"
    score = t
    preprint_pair = "preprint" in (a["type"], b["type"]) and a["type"] != b["type"]
    if a["doi"] and b["doi"] and a["doi"] != b["doi"]:
        if preprint_pair or "10.48550/" in a["doi"] + b["doi"]:
            reason = "preprint and published version"
            score -= 3
        else:
            return None  # two different registered works
    if a["year"] and b["year"] and abs(a["year"] - b["year"]) > 1 and not preprint_pair:
        score -= 12
    if a["fa"] and b["fa"] and a["fa"] != b["fa"] and fuzz.ratio(a["fa"], b["fa"]) < 80:
        score -= 15
    return score, reason


def completeness(it: dict, n_attachments: int = 0) -> float:
    d = it["data"]
    filled = sum(1 for k, v in d.items() if v not in ("", None, [], {}) and k not in ("key", "version", "dateModified"))
    return filled + 5 * n_attachments + (3 if d.get("DOI") else 0)
