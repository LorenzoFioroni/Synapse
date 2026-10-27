"""Convert provider records to Synapse fields; no HTTP or plugin lifecycle state."""

import re

from backend.plugins import scholarly

join_authors = scholarly.join_authors
parse_display_name = scholarly.parse_display_name
strip_tags = scholarly.strip_tags

ARXIV_NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def _four_digits(s: str | None) -> str:
    """The first run of four digits in ``s`` (Open Library's dates are free text), or ''."""
    m = re.search(r"\d{4}", s or "")
    return m.group(0) if m else ""


def crossref_to_item(m: dict) -> dict:
    def first(key):
        v = m.get(key)
        return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else "")

    year = ""
    for k in ("published-print", "published-online", "issued", "created"):
        dp = (m.get(k) or {}).get("date-parts")
        if dp and dp[0] and dp[0][0]:
            year = str(dp[0][0])
            break
    ctype = m.get("type", "")
    itype = {
        "journal-article": "journalArticle",
        "book": "book",
        "monograph": "book",
        "edited-book": "book",
        "book-chapter": "bookSection",
        "proceedings-article": "conferencePaper",
        "dissertation": "thesis",
        "report": "report",
        "posted-content": "preprint",
        "dataset": "dataset",
    }.get(ctype, "journalArticle")
    authors = [(a.get("family") or a.get("name") or "", a.get("given") or "") for a in m.get("author") or []]
    issn = m.get("ISSN") or []
    isbn = m.get("ISBN") or []
    lic = m.get("license") or []
    return {
        k: v
        for k, v in {
            "item_type": itype,
            "title": strip_tags(first("title")),
            "authors": join_authors(authors),
            "year": year,
            "journal": first("container-title"),
            "volume": m.get("volume", ""),
            "issue": m.get("issue", ""),
            "pages": (m.get("page") or "").replace("–", "-"),
            "publisher": m.get("publisher", ""),
            "doi": m.get("DOI", ""),
            "url": m.get("URL", ""),
            "issn": ", ".join(issn),
            "isbn": ", ".join(isbn),
            "abstract": strip_tags(m.get("abstract", "")),
            "short_title": first("short-title"),
            "language": m.get("language", ""),
            "license": lic[0].get("URL", "") if lic else "",
            "library_catalog": "Crossref",
        }.items()
        if v
    }


def arxiv_item(entry, arxiv_id: str) -> dict:
    ns = ARXIV_NS
    t = lambda path: " ".join((entry.findtext(path, default="", namespaces=ns) or "").split())
    authors = [
        parse_display_name(a.findtext("a:name", default="", namespaces=ns)) for a in entry.findall("a:author", ns)
    ]
    item = {
        "item_type": "preprint",
        "title": t("a:title"),
        "authors": join_authors(authors),
        "year": t("a:published")[:4],
        "abstract": t("a:summary"),
        "journal": "arXiv",
        "archive": "arXiv",
        "archive_id": f"arXiv:{arxiv_id}",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "doi": t("x:doi") or f"10.48550/arXiv.{arxiv_id}",
        "library_catalog": "arXiv.org",
    }
    jref = t("x:journal_ref")
    if jref:
        item["extra"] = f"Published as: {jref}"
    return {k: v for k, v in item.items() if v}


def openlibrary_to_item(d: dict, isbn: str) -> dict:
    return {
        k: v
        for k, v in {
            "item_type": "book",
            "title": d.get("title", "") + (f": {d['subtitle']}" if d.get("subtitle") else ""),
            "authors": join_authors(parse_display_name(a.get("name", "")) for a in d.get("authors") or []),
            "year": _four_digits(d.get("publish_date")),
            "publisher": ", ".join(p.get("name", "") for p in d.get("publishers") or []),
            "place": ", ".join(p.get("name", "") for p in d.get("publish_places") or []),
            "isbn": isbn,
            "url": d.get("url", ""),
            "library_catalog": "Open Library",
        }.items()
        if v
    }
