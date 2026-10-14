"""EndNote import and export: tagged "refer" format (.enw) and EndNote XML."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

from .common import join_authors, parse_display_name, split_authors, split_keywords, year_from

# ------------------------------------------------------------------ types
ENW_TYPE_IN = {
    "journal article": "journalArticle",
    "magazine article": "journalArticle",
    "newspaper article": "journalArticle",
    "electronic article": "journalArticle",
    "book": "book",
    "edited book": "book",
    "electronic book": "book",
    "book section": "bookSection",
    "conference proceedings": "conferencePaper",
    "conference paper": "conferencePaper",
    "thesis": "thesis",
    "report": "report",
    "unpublished work": "manuscript",
    "manuscript": "manuscript",
    "web page": "webpage",
    "dataset": "dataset",
    "generic": "journalArticle",
}
ENW_TYPE_OUT = {
    "journalArticle": "Journal Article",
    "book": "Book",
    "bookSection": "Book Section",
    "conferencePaper": "Conference Paper",
    "thesis": "Thesis",
    "report": "Report",
    "preprint": "Unpublished Work",
    "manuscript": "Manuscript",
    "webpage": "Web Page",
    "dataset": "Dataset",
}
XML_REF_NUMBERS = {
    "Journal Article": 17,
    "Book": 6,
    "Book Section": 5,
    "Conference Paper": 47,
    "Conference Proceedings": 10,
    "Thesis": 32,
    "Report": 27,
    "Unpublished Work": 34,
    "Manuscript": 36,
    "Web Page": 12,
    "Dataset": 59,
    "Generic": 13,
}


# --------------------------------------------------------------- tagged (.enw)
def parse(text: str) -> list[dict]:
    records: list[dict] = []
    cur: dict = {}
    last = None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿").split("\n"):
        m = re.match(r"^%(\S)\s?(.*)$", raw)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag == "0" and cur:
                records.append(cur)
                cur = {}
            cur.setdefault(tag, []).append(val)
            last = tag
        elif not raw.strip():
            if cur:
                records.append(cur)
            cur, last = {}, None
        elif cur and last:
            cur[last][-1] += " " + raw.strip()
    if cur:
        records.append(cur)
    return [_enw_item(r) for r in records if any(k != "0" for k in r)]


def _f(r, tag) -> str:
    v = r.get(tag)
    return v[0] if v else ""


def _enw_item(r: dict) -> dict:
    itype = ENW_TYPE_IN.get(_f(r, "0").lower(), "journalArticle")
    sn = _f(r, "@")
    item = {
        "item_type": itype,
        "title": _f(r, "T"),
        "authors": join_authors(parse_display_name(a) for a in r.get("A", []) if a)
        or join_authors(parse_display_name(a) for a in r.get("E", []) if a),
        "journal": _f(r, "J") or _f(r, "B"),
        "year": year_from(_f(r, "D") or _f(r, "8")),
        "volume": _f(r, "V"),
        "issue": _f(r, "N"),
        "pages": _f(r, "P").replace("–", "-"),
        "abstract": _f(r, "X"),
        "doi": re.sub(r"^https?://(dx\.)?doi\.org/", "", _f(r, "R"), flags=re.I),
        "url": _f(r, "U"),
        "publisher": _f(r, "I"),
        "place": _f(r, "C"),
        "edition": _f(r, "7"),
        "series": _f(r, "S"),
        "keywords": "; ".join(k for v in r.get("K", []) for k in split_keywords(v)),
        "language": _f(r, "G"),
        "short_title": _f(r, "!"),
        "extra": "\n".join(r.get("Z", [])),
        "citation_key": _f(r, "F"),
        "call_number": _f(r, "L"),
    }
    if sn:
        item["isbn" if itype in ("book", "bookSection") else "issn"] = sn
    return {k: v for k, v in item.items() if v}


def dump(items: list[dict], *, abstract: bool = True, **_) -> str:
    out = []
    for it in items:
        lines = [f"%0 {ENW_TYPE_OUT.get(it.get('item_type') or 'journalArticle', 'Generic')}"]

        def add(tag, val):
            if val not in (None, ""):
                lines.append(f"%{tag} {' '.join(str(val).split())}")

        add("T", it.get("title"))
        for fam, giv in split_authors(it.get("authors")):
            add("A", f"{fam}, {giv}" if giv else fam)
        add("B" if it.get("item_type") in ("bookSection", "conferencePaper") else "J", it.get("journal"))
        add("D", it.get("year"))
        add("V", it.get("volume"))
        add("N", it.get("issue"))
        add("P", it.get("pages"))
        add("I", it.get("publisher"))
        add("C", it.get("place"))
        add("7", it.get("edition"))
        add("S", it.get("series"))
        add("@", it.get("isbn") or it.get("issn"))
        add("R", it.get("doi"))
        add("U", it.get("url"))
        add("G", it.get("language"))
        add("!", it.get("short_title"))
        for kw in split_keywords(it.get("keywords")):
            add("K", kw)
        if abstract:
            add("X", it.get("abstract"))
        add("Z", it.get("extra"))
        add("F", it.get("citation_key"))
        out.append("\n".join(lines))
    return "\n\n".join(out) + ("\n" if out else "")


def sniff(text: str) -> bool:
    return bool(re.search(r"^%0 ", text.lstrip("﻿"), re.M))


# -------------------------------------------------------------------- XML
def _txt(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def parse_xml(text: str) -> list[dict]:
    root = ET.fromstring(text.lstrip("﻿").encode("utf-8"))
    items = []
    for rec in root.iter("record"):
        rt = rec.find("ref-type")
        tname = (rt.get("name") if rt is not None else "") or ""
        itype = ENW_TYPE_IN.get(tname.lower(), "journalArticle")
        authors = [parse_display_name(_txt(a)) for a in rec.findall("./contributors/authors/author")]
        if not authors:
            authors = [parse_display_name(_txt(a)) for a in rec.findall("./contributors/secondary-authors/author")]
        isbn = _txt(rec.find("isbn"))
        item = {
            "item_type": itype,
            "title": _txt(rec.find("./titles/title")),
            "authors": join_authors(authors),
            "journal": _txt(rec.find("./titles/secondary-title")) or _txt(rec.find("./periodical/full-title")),
            "short_title": _txt(rec.find("./titles/short-title")),
            "series": _txt(rec.find("./titles/tertiary-title")),
            "year": year_from(_txt(rec.find("./dates/year"))),
            "volume": _txt(rec.find("volume")),
            "issue": _txt(rec.find("number")),
            "pages": _txt(rec.find("pages")).replace("–", "-"),
            "edition": _txt(rec.find("edition")),
            "publisher": _txt(rec.find("publisher")),
            "place": _txt(rec.find("pub-location")),
            "abstract": _txt(rec.find("abstract")),
            "doi": re.sub(r"^https?://(dx\.)?doi\.org/", "", _txt(rec.find("electronic-resource-num")), flags=re.I),
            "url": _txt(rec.find("./urls/related-urls/url")) or _txt(rec.find("./urls/web-urls/url")),
            "keywords": "; ".join(_txt(k) for k in rec.findall("./keywords/keyword") if _txt(k)),
            "language": _txt(rec.find("language")),
            "extra": _txt(rec.find("notes")),
            "citation_key": _txt(rec.find("label")),
            "call_number": _txt(rec.find("call-num")),
        }
        if isbn:
            item["isbn" if itype in ("book", "bookSection") else "issn"] = isbn
        items.append({k: v for k, v in item.items() if v})
    return items


def dump_xml(items: list[dict], *, abstract: bool = True, **_) -> str:
    def el(tag, val):
        return f"<{tag}>{xml_escape(str(val))}</{tag}>" if val not in (None, "") else ""

    recs = []
    for n, it in enumerate(items, 1):
        tname = ENW_TYPE_OUT.get(it.get("item_type") or "journalArticle", "Generic")
        authors = "".join(el("author", f"{f}, {g}" if g else f) for f, g in split_authors(it.get("authors")))
        kws = "".join(el("keyword", k) for k in split_keywords(it.get("keywords")))
        parts = [
            f"<rec-number>{n}</rec-number>",
            f'<ref-type name="{tname}">{XML_REF_NUMBERS.get(tname, 13)}</ref-type>',
            f"<contributors><authors>{authors}</authors></contributors>" if authors else "",
            "<titles>"
            + el("title", it.get("title"))
            + el("secondary-title", it.get("journal"))
            + el("tertiary-title", it.get("series"))
            + el("short-title", it.get("short_title"))
            + "</titles>",
            f"<periodical>{el('full-title', it.get('journal'))}</periodical>"
            if it.get("journal") and tname == "Journal Article"
            else "",
            el("pages", it.get("pages")),
            el("volume", it.get("volume")),
            el("number", it.get("issue")),
            el("edition", it.get("edition")),
            f"<keywords>{kws}</keywords>" if kws else "",
            f"<dates>{el('year', it.get('year'))}</dates>" if it.get("year") else "",
            el("pub-location", it.get("place")),
            el("publisher", it.get("publisher")),
            el("isbn", it.get("isbn") or it.get("issn")),
            el("abstract", it.get("abstract")) if abstract else "",
            el("notes", it.get("extra")),
            el("label", it.get("citation_key")),
            el("electronic-resource-num", it.get("doi")),
            f"<urls><related-urls>{el('url', it.get('url'))}</related-urls></urls>" if it.get("url") else "",
            el("language", it.get("language")),
        ]
        recs.append("<record>" + "".join(p for p in parts if p) + "</record>")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<xml><records>\n' + "\n".join(recs) + "\n</records></xml>\n"


def sniff_xml(text: str) -> bool:
    head = text.lstrip("﻿")[:2000]
    return "<record" in head and ("<xml" in head or "<records" in head)
