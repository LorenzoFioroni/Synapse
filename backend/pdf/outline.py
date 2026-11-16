"""A PDF's outline (sections with PDF page numbers) and its figure and table captions.

The outline comes from the PDF's bookmarks when it has them; otherwise from its printed table of
contents, whose page numbers are the journal's and are matched to PDF pages by finding the headings.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path

from .regions import CAPTION

CONTENTS_SEARCH_PAGES = 12  # a table of contents sits in the front matter
CONTENTS_MAX_PAGES = 4
MIN_ENTRIES = 4
SPARSE = 6  # an outline with fewer entries than this (a letter's bookmarks) is completed from the page text
_CONTENTS = re.compile(r"^\W*(?:table\s+of\s+)?contents\W*$", re.I)
_ENTRY = re.compile(r"^(?P<title>\S.*?\S)(?:\s*[.·…]{2,}\s*|\s+)(?P<page>\d{1,4})$")
_RUN_IN = re.compile(r"^([A-Z][\w'’,:()/\- ]{2,60}?)\.\s?(?:[—–]|--?)\s?\S")  # "Introduction.—We study …"
_ROMAN_HEAD = re.compile(r"^[IVX]{1,5}\.\s+[A-Z][A-Z0-9 ,:()/\-]{3,70}$")  # "II. MODEL AND METHODS"
_NUMBERED_HEAD = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2}){0,2}|[A-Z])\.?\s+([A-Z][A-Za-z0-9 ,:()/\-]{3,70})$")
_REFERENCES = re.compile(r"^\W*(?:references|bibliography|literature cited|reference list)\W*$", re.I)
CAPTION_CHARS = 220
_NUMBER = re.compile(r"^(?P<num>[IVX]+|[A-Z]|\d+(?:\.\d+)*)\.?\s+(?P<rest>\S.*)$")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).casefold().split())


def bookmarks(path: Path | str) -> list[dict]:
    """The PDF's bookmarks as [{"level", "title", "page"}] (page counted from 1); [] if it has none."""
    import pypdf

    try:
        reader = pypdf.PdfReader(str(path))
        out: list[dict] = []

        def walk(items, level: int) -> None:
            for item in items:
                if isinstance(item, list):  # the children of the item before it
                    walk(item, level + 1)
                    continue
                title = " ".join(str(getattr(item, "title", "") or "").split())
                try:
                    page = reader.get_destination_page_number(item)
                except Exception:
                    continue
                if title and page is not None and page >= 0:
                    out.append({"level": level, "title": title, "page": page + 1})

        walk(reader.outline, 1)
        return out
    except Exception:
        return []


def _contents_entries(pages: list[str]) -> list[dict]:
    """Entries of the printed table of contents: {"title", "num", "printed", "page_index"} or []."""
    start = next(
        (
            i
            for i, text in enumerate(pages[:CONTENTS_SEARCH_PAGES])
            if any(_CONTENTS.match(line) for line in text.splitlines())
        ),
        None,
    )
    if start is None:
        return []
    entries: list[dict] = []
    for i in range(start, min(start + CONTENTS_MAX_PAGES, len(pages))):
        found = []
        for line in pages[i].splitlines():
            m = _ENTRY.match(line.strip())
            if m and not _CONTENTS.match(line):
                found.append({"title": m.group("title"), "printed": int(m.group("page")), "page_index": i})
        if i > start and len(found) < 3:
            break
        entries += found
    return entries


def _levels(entries: list[dict]) -> None:
    """Add "level" and the title without its numbering ("A." / "1." / "2.3"): by the numbering's style,
    in the order the styles first appear (I. then A. then 1.), or by the depth of a dotted number."""
    seen: list[str] = []
    for e in entries:
        m = _NUMBER.match(e["title"])
        if not m:
            e["level"], e["name"] = 1, e["title"]
            continue
        num = m.group("num")
        if "." in num:
            e["level"] = num.count(".") + 1
        else:
            style = "roman" if re.fullmatch(r"[IVX]+", num) else "letter" if num.isalpha() else "number"
            if style not in seen:
                seen.append(style)
            e["level"] = seen.index(style) + 1
        e["name"] = m.group("rest")


def _page_offset(entries: list[dict], pages: list[str]) -> int | None:
    """PDF page index minus printed page number, found by where the headings are. None if unsure."""
    skip = {e["page_index"] for e in entries}
    lines = [[_fold(l) for l in text.splitlines()] if i not in skip else [] for i, text in enumerate(pages)]
    votes: Counter[int] = Counter()
    checked = 0
    for e in entries[:60]:
        name = _fold(e["name"])
        if len(name) < 6:
            continue
        checked += 1
        hit = {
            j for j, page_lines in enumerate(lines) if any(name in l and len(l) <= len(name) + 12 for l in page_lines)
        }
        votes.update({j - (e["printed"] - 1) for j in hit})  # printed 1 is page index 0 when the offset is 0
    if not votes:
        return None
    offset, n = votes.most_common(1)[0]
    return offset if n >= max(2, checked // 3) else None


def contents_page_outline(pages: list[str]) -> tuple[list[dict], bool]:
    """(entries, matched): the outline from a printed table of contents. ``matched`` is False when the
    printed page numbers could not be tied to PDF pages (they are then returned as printed)."""
    entries = _contents_entries(pages)
    if len(entries) < MIN_ENTRIES:
        return [], False
    _levels(entries)
    offset = _page_offset(entries, pages)
    out = []
    for e in entries:
        page = e["printed"] + (offset if offset is not None else 0)
        if 1 <= page <= len(pages):
            out.append({"level": e["level"], "title": e["title"], "page": page})
    return out, offset is not None


def captions(pages: list[str]) -> list[dict]:
    """Figure and table captions from the page text: {"label", "page", "text"}, the first of each label."""
    out: list[dict] = []
    seen: set[str] = set()
    for n, text in enumerate(pages, 1):
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = CAPTION.match(line)
            if not m:
                continue
            rest = line[m.end() :].lstrip()
            if rest[:1].islower() and not m.group(0).rstrip().endswith((":", ".", "|")):
                continue  # "Fig. 3 shows …" in running text
            kind = "Table" if m.group(1).lower().startswith("tab") else "Figure"
            label = f"{kind} {m.group(2)}"
            if label not in seen:
                seen.add(label)
                out.append({"label": label, "page": n, "text": _caption_text(lines, i)})
    return out


def _caption_text(lines: list[str], i: int) -> str:
    """A caption's first line and the lines that continue it (up to a blank line or another caption),
    cut at a word boundary: the first line alone often ends mid-sentence."""
    text = " ".join(lines[i].split())
    for nxt in lines[i + 1 : i + 5]:
        if len(text) >= CAPTION_CHARS or not nxt.strip() or CAPTION.match(nxt):
            break
        text += " " + " ".join(nxt.split())
    if len(text) <= CAPTION_CHARS:
        return text
    return text[:CAPTION_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def text_headings(pages: list[str]) -> list[dict]:
    """Headings found in the page text, for papers whose bookmarks do not show them (letters, with
    "Introduction.—" run into the paragraph): [{"level", "title", "page"}]. Approximate: a line that looks
    like a heading (a run-in title before a dash, "II. MODEL", "3 Results") and is short."""
    out: list[dict] = []
    seen: set[str] = set()
    for n, text in enumerate(pages, 1):
        for line in text.splitlines():
            line = " ".join(line.split())
            if _REFERENCES.match(line):
                return out  # what follows is the reference list ("3. Gedik, Observation of …")
            m = _RUN_IN.match(line)
            title, level = (m.group(1), 1) if m else (None, 1)
            if title is None and (_ROMAN_HEAD.match(line) or (_NUMBERED_HEAD.match(line) and not line.endswith("."))):
                title = line
                num = re.match(r"^(\d+(?:\.\d+)*)", line)
                level = num.group(1).count(".") + 1 if num else 1
            if not title or len(title.split()) > 9 or sum(c.isdigit() for c in title) > len(title) // 4:
                continue
            if title is line and "," in line and re.match(r"^[A-Z]\.", line):
                continue  # "N. Gedik, …": a reference's first author, not the heading "A. Model"
            key = _fold(title)
            if key not in seen:
                seen.add(key)
                out.append({"level": level, "title": title, "page": n})
    return out


def with_text_headings(entries: list[dict], pages: list[str]) -> tuple[list[dict], bool]:
    """``entries`` (bookmarks or contents) completed with headings from the page text when there are few of
    them. Returns (entries, completed)."""
    if len(entries) >= SPARSE:
        return entries, False
    found = text_headings(pages)
    known = {_fold(e["title"]) for e in entries}
    extra = [h for h in found if _fold(h["title"]) not in known]
    if len(found) < 2 or not extra:
        return entries, False
    return sorted(entries + extra, key=lambda e: e["page"]), True
