"""Reference lists from arXiv's HTML version of a paper (arxiv.org/html/<id>, made with LaTeXML for
most LaTeX submissions). Semantic Scholar and OpenAlex usually know a paper's references only days
after it is announced; the HTML version has them from the first day.

LaTeXML writes the bibliography as ``<section class="ltx_bibliography">`` with one
``<li class="ltx_bibitem">`` per entry, its parts in ``<span class="ltx_bibblock">``. From each entry
we keep the text (for matching by title) and any DOI or arXiv id in its text or links.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import requests

from backend.library.identifiers import ARXIV_ID, norm_arxiv, norm_doi
from backend.online.http import TIMEOUT, LookupFailed, arxiv_wait

URL = "https://arxiv.org/html/{}"
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}|\\^`]+)", re.I)
ARXIV_RE = re.compile(r"(?:arxiv[:\s./]*(?:abs/|pdf/)?|arxiv\.org/(?:abs|pdf)/)" + ARXIV_ID.pattern, re.I)
YEAR_RE = re.compile(r"\((\d{4})\)|\b(19\d\d|20\d\d)\b")
# what is left of a quoted or italic title: the longest stretch between separators
TITLE_RE = re.compile(r"[“\"]([^”\"]{15,300})[”\"]")


class _Bib(HTMLParser):
    """Collects the text and link targets of each ``ltx_bibitem``."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._depth = 0  # depth inside the current bibitem (0: outside)
        self._italic: list[int] = []  # depths of open italic/emphasis spans
        self._cur: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if self._cur is None:
            if tag == "li" and "ltx_bibitem" in cls.split():
                self._cur = {"text": [], "links": [], "italic": [], "id": a.get("id")}
                self._depth = 1
            return
        if tag not in ("br", "img", "hr", "wbr"):
            self._depth += 1
        if tag == "a" and a.get("href"):
            self._cur["links"].append(a["href"])
        if "ltx_font_italic" in cls.split() or tag in ("i", "em"):
            self._italic.append(self._depth)
            self._cur["italic"].append("")
        if "ltx_tag_bibitem" in cls.split():
            self._italic.append(-self._depth)  # the "[12]" label: left out of the text

    def handle_endtag(self, tag):
        if self._cur is None or tag in ("br", "img", "hr", "wbr"):
            return
        if self._italic and abs(self._italic[-1]) == self._depth:
            self._italic.pop()
        self._depth -= 1
        if self._depth == 0:
            self.items.append(self._cur)
            self._cur = None
            self._italic = []

    def handle_data(self, data):
        if self._cur is None:
            return
        if any(d < 0 for d in self._italic):
            return
        self._cur["text"].append(data)
        if self._italic and self._italic[-1] > 0:
            self._cur["italic"][-1] += data


def _clean(s: str) -> str:
    return " ".join(s.split())


def parse(html: str) -> list[dict]:
    """The references in an arXiv HTML page, in order: each with ``raw`` text and, when found,
    ``doi``, ``arxiv_id``, ``title`` and ``year`` (the keys the citation store keeps)."""
    start = html.find("ltx_bibliography")
    if start < 0:
        return []
    p = _Bib()
    p.feed(html[max(0, html.rfind("<", 0, start)) :])
    p.close()
    refs = []
    for it in p.items:
        raw = _clean("".join(it["text"]))
        if not raw:
            continue
        where = " ".join([raw, *it["links"]])
        doi = None
        for dm in DOI_RE.finditer(where):
            d = norm_doi(dm.group(1).rstrip(".,;)]"))
            if d and not d.startswith("10.48550/"):
                doi = d
                break
        arx = None
        am = ARXIV_RE.search(where) or re.search(r"10\.48550/arxiv\.(\S+)", where, re.I)
        if am:
            arx = norm_arxiv(am.group(1))
        title = None
        italic = [_clean(t) for t in it["italic"] if len(_clean(t)) >= 15]
        tm = TITLE_RE.search(raw)
        if tm:
            title = _clean(tm.group(1)).rstrip(",.")
        elif italic:
            title = max(italic, key=len).rstrip(",.")
        ym = YEAR_RE.search(raw)
        year = int(ym.group(1) or ym.group(2)) if ym else None
        refs.append(
            {
                "doi": doi,
                "arxiv_id": arx,
                "openalex_id": None,
                "s2_id": None,
                "title": title,
                "authors": None,
                "year": year,
                "venue": None,
                "raw": raw[:1000],
            }
        )
    return refs


def fetch(arxiv_id: str) -> list[dict]:
    """The references of arXiv paper ``arxiv_id`` from its HTML version; [] when it has none
    (no HTML version, or one without a bibliography). Raises LookupFailed when arXiv cannot be
    reached."""
    aid = norm_arxiv(arxiv_id)
    if not aid:
        return []
    arxiv_wait()
    try:
        r = requests.get(URL.format(aid), timeout=TIMEOUT, headers={"User-Agent": "Synapse/2.0 (reference lists)"})
    except requests.RequestException as e:
        raise LookupFailed(f"arXiv could not be reached: {e}") from e
    if r.status_code == 404:
        return []
    if r.status_code >= 400:
        raise LookupFailed(f"arXiv answered {r.status_code}")
    return parse(r.text)
