"""arXiv's daily listings, as its RSS feeds give them (rss.arxiv.org/rss/<categories>): one feed for
several categories joined by "+", rebuilt each announcement day (Monday to Friday, around
midnight US Eastern), empty on days without an announcement.

Each item's description starts "arXiv:2610.12345v1 Announce Type: new" and then the abstract; the
type is new, cross (listed in another category first), replace or replace-cross (a new version).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

RSS = "https://rss.arxiv.org/rss/"
CATEGORY_RE = re.compile(r"^[a-z][a-z-]*(\.[A-Za-z][A-Za-z-]*)?$")
NS = {"arxiv": "http://arxiv.org/schemas/atom", "dc": "http://purl.org/dc/elements/1.1/"}
DESC_RE = re.compile(r"^\s*arXiv:(\S+?)(?:v\d+)?\s+Announce Type:\s*(\S+)\s*(?:Abstract:\s*)?(.*)$", re.S)
ID_RE = re.compile(r"(?:arXiv\.org:|abs/)([^\s/v]+(?:/\d{7})?)(?:v\d+)?$")


def url(categories: list[str]) -> str:
    return RSS + "+".join(categories)


def check_categories(values: list[str]) -> tuple[list[str], list[str]]:
    """(the usable categories, those that are not category names)."""
    good, bad = [], []
    for v in values:
        v = v.strip()
        if v:
            (good if CATEGORY_RE.match(v) else bad).append(v)
    return good, bad


def _authors(creator: str) -> list[str]:
    names = re.split(r",\s*|\s+and\s+", " ".join((creator or "").split()))
    return [n.strip() for n in names if n.strip()]


def parse(xml: str) -> tuple[str | None, list[dict]]:
    """(the announcement day as YYYY-MM-DD, its entries): each with ``arxiv``, ``kind``, ``title``,
    ``authors`` (a list of names), ``abstract`` and ``categories``."""
    root = ET.fromstring(xml)
    channel = root.find("channel")
    if channel is None:
        return None, []
    day = None
    stamp = channel.findtext("pubDate") or channel.findtext("lastBuildDate")
    if stamp:
        try:
            day = parsedate_to_datetime(stamp).date().isoformat()
        except (TypeError, ValueError):
            day = None
    out = []
    for it in channel.findall("item"):
        desc = it.findtext("description") or ""
        m = DESC_RE.match(desc)
        ident = m.group(1) if m else None
        if not ident:
            idm = ID_RE.search((it.findtext("guid") or it.findtext("link") or "").strip())
            ident = idm.group(1) if idm else None
        if not ident:
            continue
        kind = (it.findtext("arxiv:announce_type", namespaces=NS) or (m.group(2) if m else "new")).strip()
        out.append(
            {
                "arxiv": ident,
                "kind": kind,
                "title": " ".join((it.findtext("title") or "").split()),
                "authors": _authors(it.findtext("dc:creator", namespaces=NS) or ""),
                "abstract": " ".join((m.group(3) if m else "").split()),
                "categories": [c.text.strip() for c in it.findall("category") if c.text and c.text.strip()],
            }
        )
    return day, out
