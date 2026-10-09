"""CSL-JSON import and export (used by Zotero, Pandoc, citeproc)."""
from __future__ import annotations

import json

from .common import join_authors, split_authors, split_keywords, year_from

TYPE_OUT = {
    "journalArticle": "article-journal", "book": "book", "bookSection": "chapter",
    "conferencePaper": "paper-conference", "thesis": "thesis", "report": "report",
    "preprint": "article", "manuscript": "manuscript", "webpage": "webpage", "dataset": "dataset",
}
TYPE_IN = {v: k for k, v in TYPE_OUT.items()}
TYPE_IN.update({"article-magazine": "journalArticle", "article-newspaper": "journalArticle",
                "post-weblog": "webpage", "post": "webpage"})

SIMPLE = [("volume", "volume"), ("issue", "issue"), ("pages", "page"), ("publisher", "publisher"),
          ("place", "publisher-place"), ("edition", "edition"), ("series", "collection-title"),
          ("isbn", "ISBN"), ("issn", "ISSN"), ("doi", "DOI"), ("url", "URL"), ("abstract", "abstract"),
          ("language", "language"), ("short_title", "title-short"), ("extra", "note"),
          ("pmid", "PMID"), ("pmcid", "PMCID"), ("call_number", "call-number"), ("archive", "archive"),
          ("archive_id", "archive_location")]


def parse(text: str) -> list[dict]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("items") or [data]
    items = []
    for d in data:
        if not isinstance(d, dict):
            continue
        authors = [(a.get("family") or a.get("literal") or "", a.get("given") or "") for a in d.get("author") or []]
        issued = d.get("issued") or {}
        year = ""
        if isinstance(issued, dict):
            parts = issued.get("date-parts") or [[]]
            year = str(parts[0][0]) if parts and parts[0] else year_from(issued.get("raw") or issued.get("literal"))
        item = {
            "item_type": TYPE_IN.get(d.get("type", ""), "journalArticle"),
            "title": d.get("title", ""),
            "authors": join_authors(authors),
            "year": year,
            "journal": d.get("container-title", ""),
            "citation_key": d.get("citation-key") or (d["id"] if isinstance(d.get("id"), str) else ""),
            "keywords": "; ".join(split_keywords(d.get("keyword", ""))),
        }
        for ours, theirs in SIMPLE:
            if d.get(theirs):
                item[ours] = str(d[theirs])
        items.append({k: v for k, v in item.items() if v})
    return items


def dump(items: list[dict], *, abstract: bool = True, **_) -> str:
    out = []
    for it in items:
        d = {"id": it.get("citation_key") or it.get("id"), "type": TYPE_OUT.get(it.get("item_type") or "", "article")}
        if it.get("citation_key"):
            d["citation-key"] = it["citation_key"]
        if it.get("title"):
            d["title"] = it["title"]
        authors = [{"family": f, "given": g} if g else {"literal": f} for f, g in split_authors(it.get("authors"))]
        if authors:
            d["author"] = authors
        if it.get("journal"):
            d["container-title"] = it["journal"]
        y = year_from(it.get("year"))
        if y:
            d["issued"] = {"date-parts": [[int(y)]]}
        for ours, theirs in SIMPLE:
            if it.get(ours) and (abstract or ours != "abstract"):
                d[theirs] = str(it[ours])
        if it.get("keywords"):
            d["keyword"] = ", ".join(split_keywords(it["keywords"]))
        out.append(d)
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def sniff(text: str) -> bool:
    t = text.lstrip("﻿").lstrip()
    if not t.startswith(("[", "{")):
        return False
    try:
        data = json.loads(t)
    except ValueError:
        return False
    first = data[0] if isinstance(data, list) and data else data
    return isinstance(first, dict) and ("title" in first or "type" in first)
