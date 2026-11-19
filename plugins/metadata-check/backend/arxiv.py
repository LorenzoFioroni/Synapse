"""Newer arXiv versions of the preprints in the library, and published versions found by title
(on Crossref, for preprints whose arXiv record names no DOI).

arXiv's API answers for many papers at once: each entry's id ends with its latest version
(``…/abs/2101.00001v3``) and ``updated`` is when that version was posted. Which version the
library holds comes, best first, from the PDF's margin stamp (``arXiv:2101.00001v2``), from a
replacement this plugin made, from a version written in the record, and otherwise from dates:
a PDF added before the latest version was posted is older than it.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from backend.plugins import scholarly

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}
BATCH = 50  # ids per request; arXiv's API takes long lists, kept modest so one failure costs little
ID = r"(\d{4}\.\d{4,5}|[a-z\-]{1,32}(?:\.[A-Z]{2})?/\d{7})"


def real_doi(value) -> str:
    """A DOI that is not arXiv's own (10.48550/arXiv.…), lower case; "" when there is none."""
    doi = str(value or "").strip().lower().removeprefix("https://doi.org/")
    return "" if not doi or doi.startswith("10.48550/arxiv.") else doi


def bare_id(item: dict) -> str | None:
    """The item's arXiv id without version, as arXiv writes it (case kept for old-style ids)."""
    found = scholarly.arxiv_id_of(item)
    m = re.search(ID, found or "", re.I)
    return m.group(1) if m else None


def checks_versions(item: dict) -> bool:
    """A preprint not yet published elsewhere: its PDF is an arXiv version."""
    return bool(bare_id(item)) and not real_doi(item.get("doi")) and pdf_of(item) is not None


def pdf_of(item: dict) -> dict | None:
    """The first PDF stored in the library (what a citation of the item opens)."""
    for att in item.get("attachments") or []:
        url = str(att.get("url") or "")
        if att.get("type") == "pdf" and url and not url.startswith(("http://", "https://")):
            return att
    return None


def parse_feed(xml: bytes | str) -> dict[str, dict]:
    """{bare id (lower case): {"latest": version, "updated": ISO time}} from an API answer."""
    out: dict[str, dict] = {}
    root = ET.fromstring(xml)
    for entry in root.findall("a:entry", NS):
        abs_url = entry.findtext("a:id", default="", namespaces=NS)
        m = re.search(ID + r"v(\d+)\s*$", abs_url.split("/abs/")[-1], re.I)
        if not m:
            continue  # arXiv's error entries have no version
        out[m.group(1).lower()] = {
            "latest": int(m.group(2)),
            "updated": (entry.findtext("a:updated", default="", namespaces=NS) or "").strip(),
        }
    return out


def latest(http, ids: list[str]) -> dict[str, dict]:
    """The latest version of each paper (missing for ids arXiv does not know). ``plugin.http`` spaces
    requests to arXiv; Offline and HttpError reach the caller."""
    out: dict[str, dict] = {}
    for i in range(0, len(ids), BATCH):
        chunk = ids[i : i + BATCH]
        r = http.get(API, params={"id_list": ",".join(chunk), "max_results": str(len(chunk))}, cache=False)
        out.update(parse_feed(r.content))
    return out


def stamped_version(text: str, arxiv_id: str) -> int | None:
    """The version in arXiv's margin stamp of this paper (other ids on the page are citations)."""
    m = re.search(r"arXiv:\s*" + re.escape(arxiv_id) + r"v(\d+)", text or "", re.I)
    return int(m.group(1)) if m else None


def recorded_version(item: dict, arxiv_id: str) -> int | None:
    """A version written in the record (``arXiv:2101.00001v2`` in its archive ID, URL …)."""
    for field in ("archive_id", "url", "doi", "extra"):
        m = re.search(re.escape(arxiv_id) + r"v(\d+)", str(item.get(field) or ""), re.I)
        if m:
            return int(m.group(1))
    return None


def _time(value) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def newer(entry: dict | None, pdf: dict | None) -> dict | None:
    """``{"version", "have", "how"}`` when arXiv has a version newer than the library's PDF, else None.
    ``entry`` is what the plugin keeps for the item: latest, updated, and what it knows of the PDF
    (``stamp``, ``replaced``, ``recorded``); ``have`` is None when only dates tell."""
    if not entry or not pdf or not entry.get("latest"):
        return None
    top = int(entry["latest"])
    for how in ("stamp", "replaced", "recorded"):
        have = entry.get(how)
        if have:
            return {"version": top, "have": int(have), "how": how} if top > int(have) else None
    posted, added = _time(entry.get("updated")), _time(pdf.get("date_added"))
    if top > 1 and posted and added and posted > added:
        return {"version": top, "have": None, "how": "date"}
    return None


def describe(found: dict) -> str:
    """What the review dialog says about the PDF the library has."""
    have = found.get("have")
    if found.get("how") == "stamp":
        return f"Your PDF is v{have}, by its arXiv stamp."
    if have:
        return f"Your PDF is v{have}."
    return "Your PDF was added before this version was posted."


CROSSREF = "https://api.crossref.org/works"
# Crossref's kinds of record that are a published version, not another preprint.
PUBLISHED_TYPES = "journal-article,proceedings-article,book-chapter"
SAME_TITLE = 0.9


def _plain(name: str) -> str:
    """A family name without accents, case or punctuation."""
    s = unicodedata.normalize("NFKD", name or "")
    return re.sub(r"[^a-z]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def _year(value) -> int | None:
    m = re.match(r"\d{4}", str(value or ""))
    return int(m.group(0)) if m else None


def published_by_title(http, current: dict, email: str = "") -> str | None:
    """The DOI of the published version of a preprint, from a Crossref search by its title: a
    journal, proceedings or book record with nearly the same title, the same first author and not
    older than the preprint. None when there is none (or no title or author to compare)."""
    pairs = scholarly.split_authors(str(current.get("authors") or ""))
    title, first = str(current.get("title") or ""), _plain(pairs[0][0]) if pairs else ""
    if not title or not first:
        return None
    params = {"query.bibliographic": title, "filter": "type:" + PUBLISHED_TYPES.replace(",", ",type:"), "rows": "5"}
    if email:
        params["mailto"] = email
    found = (http.get(CROSSREF, params=params).json().get("message") or {}).get("items") or []
    since = _year(current.get("year"))
    for work in found:
        if scholarly.title_similarity(title, (work.get("title") or [""])[0]) < SAME_TITLE:
            continue
        if _plain(((work.get("author") or [{}])[0]).get("family") or "") != first:
            continue
        issued = ((work.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        if since and issued and int(issued) < since:
            continue
        doi = real_doi(work.get("DOI"))
        if doi:
            return doi
    return None
