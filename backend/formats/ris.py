"""RIS import and export."""

from __future__ import annotations

import re

from .common import join_authors, join_pages, parse_display_name, split_authors, split_keywords, split_pages, year_from

TYPE_IN = {
    "JOUR": "journalArticle",
    "JFULL": "journalArticle",
    "MGZN": "journalArticle",
    "NEWS": "journalArticle",
    "EJOUR": "journalArticle",
    "BOOK": "book",
    "EBOOK": "book",
    "EDBOOK": "book",
    "CHAP": "bookSection",
    "ECHAP": "bookSection",
    "CONF": "conferencePaper",
    "CPAPER": "conferencePaper",
    "THES": "thesis",
    "RPRT": "report",
    "UNPB": "manuscript",
    "UNPD": "manuscript",
    "MANSCPT": "manuscript",
    "ELEC": "webpage",
    "WEB": "webpage",
    "BLOG": "webpage",
    "DATA": "dataset",
    "DBASE": "dataset",
    "GEN": "journalArticle",
}
TYPE_OUT = {
    "journalArticle": "JOUR",
    "book": "BOOK",
    "bookSection": "CHAP",
    "conferencePaper": "CONF",
    "thesis": "THES",
    "report": "RPRT",
    "preprint": "UNPB",
    "manuscript": "MANSCPT",
    "webpage": "ELEC",
    "dataset": "DATA",
}
TAG_RE = re.compile(r"^([A-Z][A-Z0-9])  -(?: (.*))?$")


def parse(text: str) -> list[dict]:
    records, cur, last = [], None, None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿").split("\n"):
        m = TAG_RE.match(raw.rstrip())
        if not m:
            if cur is not None and last and raw.strip():
                cur[last][-1] += " " + raw.strip()
            continue
        tag, val = m.group(1), (m.group(2) or "").strip()
        if tag == "TY":
            cur, last = {"TY": [val]}, "TY"
        elif tag == "ER":
            if cur is not None:
                records.append(cur)
            cur, last = None, None
        elif cur is not None:
            cur.setdefault(tag, []).append(val)
            last = tag
    if cur is not None:
        records.append(cur)
    return [_to_item(r) for r in records]


def _first(r, *tags) -> str:
    for t in tags:
        for v in r.get(t, []):
            if v:
                return v
    return ""


def _to_item(r: dict) -> dict:
    ty = _first(r, "TY").upper()
    itype = TYPE_IN.get(ty, "journalArticle")
    authors = [parse_display_name(a) for t in ("AU", "A1") for a in r.get(t, []) if a]
    if not authors:
        authors = [parse_display_name(a) for a in r.get("A2", []) if a]
    sn = _first(r, "SN")
    item = {
        "item_type": itype,
        "title": _first(r, "TI", "T1", "CT") or (_first(r, "BT") if itype == "book" else ""),
        "authors": join_authors(authors),
        "journal": _first(r, "T2", "JO", "JF", "JA", "J2") or (_first(r, "BT") if itype != "book" else ""),
        "year": year_from(_first(r, "PY", "Y1", "DA")),
        "volume": _first(r, "VL"),
        "issue": _first(r, "IS", "CP"),
        "pages": join_pages(_first(r, "SP"), _first(r, "EP")),
        "abstract": _first(r, "AB", "N2"),
        "doi": re.sub(r"^https?://(dx\.)?doi\.org/", "", _first(r, "DO"), flags=re.I),
        "url": _first(r, "UR", "L2"),
        "publisher": _first(r, "PB"),
        "place": _first(r, "CY", "PP"),
        "edition": _first(r, "ET"),
        "series": _first(r, "T3"),
        "keywords": "; ".join(k for v in r.get("KW", []) for k in split_keywords(v)),
        "language": _first(r, "LA"),
        "short_title": _first(r, "ST"),
        "extra": "\n".join(v for v in r.get("N1", []) if v),
        "citation_key": _first(r, "ID"),
        "call_number": _first(r, "CN"),
    }
    if sn:
        digits = re.sub(r"[^0-9Xx]", "", sn.split()[0] if sn.split() else sn)
        item["isbn" if itype in ("book", "bookSection") or len(digits) >= 10 else "issn"] = sn
    return {k: v for k, v in item.items() if v}


def dump(items: list[dict], *, abstract: bool = True, **_) -> str:
    out = []
    for it in items:
        lines = [f"TY  - {TYPE_OUT.get(it.get('item_type') or 'journalArticle', 'GEN')}"]

        def add(tag, val):
            if val not in (None, ""):
                lines.append(f"{tag}  - {' '.join(str(val).split()) if tag != 'N1' else str(val)}")

        add("TI", it.get("title"))
        for fam, giv in split_authors(it.get("authors")):
            add("AU", f"{fam}, {giv}" if giv else fam)
        add("T2", it.get("journal"))
        add("PY", it.get("year"))
        add("VL", it.get("volume"))
        add("IS", it.get("issue"))
        sp, ep = split_pages(it.get("pages"))
        add("SP", sp)
        add("EP", ep)
        add("PB", it.get("publisher"))
        add("CY", it.get("place"))
        add("ET", it.get("edition"))
        add("T3", it.get("series"))
        add("SN", it.get("isbn") or it.get("issn"))
        add("DO", it.get("doi"))
        add("UR", it.get("url"))
        add("LA", it.get("language"))
        add("ST", it.get("short_title"))
        for kw in split_keywords(it.get("keywords")):
            add("KW", kw)
        if abstract:
            add("AB", it.get("abstract"))
        add("N1", it.get("extra"))
        add("ID", it.get("citation_key"))
        lines.append("ER  - ")
        out.append("\n".join(lines))
    return "\n\n".join(out) + ("\n" if out else "")


def sniff(text: str) -> bool:
    return bool(re.search(r"^TY  - ", text.lstrip("﻿"), re.M))
