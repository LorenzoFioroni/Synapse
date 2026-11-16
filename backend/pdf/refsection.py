"""Where a paper's reference list is in its text.

A search for "Zhang" or "time crystal" finds every paper that *cites* such a work, because a PDF's
text includes its bibliography. ``reference_spans`` finds the reference list(s), start and end, so a
hit inside one can be told from a hit in the paper itself. Text after the list (appendices,
supplementary material, acknowledgments) is not part of it; a supplement's own list is a second span.

Two signs are used, both read from the text alone:

* a run of entries at line starts, most of which hold a year: numbered ones counting up from 1
  (``[1]``, ``1.`` or a bare ``1``), or lines that begin with an author ("Smith, J. A., 1972, …");
* a "References" heading, for the other lists that are not numbered.

When in doubt the text counts as the paper's own: a missed list leaves the search as it was, while
body text taken for a list would hide real hits.
"""

from __future__ import annotations

import re
from statistics import median

_HEADING = re.compile(
    r"^\W*(?:(?:\d{1,2}|[IVX]{1,5})\.?\s+)?"
    r"(?:references(?:\s+and\s+notes)?|notes\s+and\s+references|bibliography|literature\s+cited|works\s+cited|"
    r"reference\s+list)\W*$",
    re.I,
)
# What can follow a list: the heading of an appendix, a supplement or the end matter.
_AFTER = re.compile(
    r"^\W*(?:(?:[A-Z]|\d{1,2}|[IVX]{1,5})[.:]?\s+)?"
    r"(?:appendix|appendices|supplement(?:al|ary)\b|supporting\s+information|methods?\b|extended\s+data|"
    r"acknowledge?ments?\b|author\s+contributions?|competing\s+interests|conflicts?\s+of\s+interest|"
    r"data\s+availability|code\s+availability|additional\s+information|end\s+matter|figure\s+captions|"
    r"list\s+of\s+figures|glossary)",
    re.I,
)
_YEAR = re.compile(r"\b(?:1[6-9]|20)\d\d[a-z]?\b")
_CAPTION = re.compile(r"^\W*(?:fig(?:ure)?|table)\.?\s*(?:\d+|[IVX]+)\s*[.:|]", re.I)  # captions printed after the list
_MARKERS = {
    "bracket": re.compile(r"^[ \t]*\[[ \t]*(S?)[ \t]*(\d{1,3})[ \t]*\]", re.M),  # also "[ 1 ]", as some PDFs space it
    "dot": re.compile(r"^[ \t]*()(\d{1,3})\.(?=[ \t]*(?:$|[A-Z\"“‘']))", re.M),
    "bare": re.compile(r"^[ \t]*()(\d{1,3})[ \t]?(?=[A-Z][.\w])", re.M),  # "12A. Einstein" (a superscript number)
}
# An entry of an author–year list: "Smith, J. A., 1972, …", possibly after a dash or bullet.
_NAME = re.compile(r"^[ \t]*(?:[−–\-•*][ \t]*)?[A-Z][\w'’\-]+(?:[ \t][A-Z][\w'’\-]+)?,[ \t]+[A-Z]\.", re.M)
# (entries in a row, share of them with a year): a looser style needs more evidence
_NEEDS = {"bracket": (5, 0.5), "dot": (5, 0.6), "bare": (8, 0.7), "name": (8, 0.7)}
FIRST_NUMBERS = (1, 2, 3)  # a list may start at [2] or [3] when the extraction lost its first entries
MAX_NAME_GAP = 1500  # between two entries of an author–year list
MAX_GAP = 4000  # between two entries: one entry with many authors, or a page break with its header
MAX_SKIP = 3  # numbers missing in a row (an entry the extraction lost)
MAX_ENTRY = 900  # typical length of an entry, at most: longer "entries" are paragraphs
HEADING_BEFORE = 300  # a "References" heading this close above the first entry belongs to the list
HEADING_CHARS = 90
UNNUMBERED_FROM = 0.3  # an unnumbered list is not in the first part of a paper (a table of contents is)


def _is_list(text: str, run: list[int], style: str) -> bool:
    """Enough entries, short ones, most of them with a year."""
    need, with_year = _NEEDS[style]
    if len(run) < need:
        return False
    entries = [text[a:b] for a, b in zip(run, run[1:])]
    return (
        sum(bool(_YEAR.search(e)) for e in entries) >= with_year * len(entries)
        and median(len(e) for e in entries) <= MAX_ENTRY
    )


def _runs(text: str, style: str) -> list[list[int]]:
    """Positions of the entries of each list written in ``style``."""
    runs: list[list[int]] = []
    if style == "name":
        run: list[int] = []
        for m in _NAME.finditer(text):
            if run and m.start() - run[-1] > MAX_NAME_GAP:
                if _is_list(text, run, style):
                    runs.append(run)
                run = []
            run.append(m.start())
        if _is_list(text, run, style):
            runs.append(run)
        return runs
    found = [(m.start(), m.group(1), int(m.group(2))) for m in _MARKERS[style].finditer(text)]
    used_until = -1
    for i, (pos, prefix, n) in enumerate(found):
        if n not in FIRST_NUMBERS or pos <= used_until:
            continue
        run, cur = [pos], n
        for pos2, prefix2, n2 in found[i + 1 :]:
            if pos2 - run[-1] > MAX_GAP:
                break
            if prefix2 == prefix and cur < n2 <= cur + MAX_SKIP:
                run.append(pos2)
                cur = n2
        if _is_list(text, run, style):
            runs.append(run)
            used_until = run[-1]
    return runs


def _line_start(text: str, pos: int) -> int:
    return text.rfind("\n", 0, pos) + 1


def _is_heading(line: str, pattern: re.Pattern) -> bool:
    line = line.strip()
    if pattern is _AFTER and _CAPTION.match(line):
        return True
    return bool(
        line and len(line) <= HEADING_CHARS and not line.endswith((",", ";")) and not _YEAR.search(line)
    ) and bool(pattern.match(line))


def _next_heading(text: str, start: int, pattern: re.Pattern) -> int | None:
    """Start of the first line after ``start`` that is a heading of ``pattern``."""
    pos = text.find("\n", start)
    while pos != -1:
        end = text.find("\n", pos + 1)
        line = text[pos + 1 : end if end != -1 else len(text)]
        if _is_heading(line, pattern):
            return pos + 1
        pos = end
    return None


def _heading_above(text: str, pos: int) -> int | None:
    """Start of a "References" heading in the lines just above ``pos``."""
    at = _line_start(text, pos)
    floor = max(0, at - HEADING_BEFORE)
    while at > floor:
        prev = _line_start(text, at - 1)
        line = text[prev : at - 1]
        if _is_heading(line, _HEADING):
            return prev
        if line.strip() and not re.fullmatch(r"\W*\d*\W*", line):  # only blank lines or a page number between
            return None
        at = prev
    return None


def reference_spans(text: str) -> list[tuple[int, int]]:
    """The reference lists of ``text`` as (start, end) character ranges, in order; [] when none is found."""
    spans: list[tuple[int, int]] = []
    for style in (*_MARKERS, "name"):
        for run in _runs(text, style):
            if any(a <= run[0] < b for a, b in spans):
                continue  # already found in a stricter style
            start = _line_start(text, run[0])
            head = _heading_above(text, start)
            longest = max(b - a for a, b in zip(run, run[1:]))
            # The last entry has no next one to end it. It ends with the first line that closes a sentence
            # ("… 100 (2001)."), at an appendix heading, or at the length of the longest entry before it (plus a
            # margin), whichever comes first: cut short, its last words count as the paper's own text.
            cap = min(len(text), run[-1] + int(1.5 * longest))
            line_end = text.find("\n", run[-1])
            while line_end != -1 and line_end < cap and not text[:line_end].rstrip().endswith((".", ")", "]")):
                line_end = text.find("\n", line_end + 1)
            closed = line_end + 1 if line_end != -1 else len(text)
            after = _next_heading(text, run[-1], _AFTER)
            end = min(closed, max(cap, text.find("\n", run[-1]) + 1), after if after is not None else len(text))
            spans.append((head if head is not None else start, end))
    # Lists without numbers: a heading far enough into the paper, followed by years.
    pos = 0
    for line in text.splitlines(keepends=True):
        here, pos = pos, pos + len(line)
        if here < UNNUMBERED_FROM * len(text) or not _is_heading(line, _HEADING):
            continue
        if any(a <= here <= b for a, b in spans) or any(0 <= a - pos <= HEADING_BEFORE for a, _ in spans):
            continue
        if len(_YEAR.findall(text[pos : pos + 1500])) < 3:
            continue
        after = _next_heading(text, pos, _AFTER)
        spans.append((here, after if after is not None else len(text)))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def split(text: str) -> tuple[str, str]:
    """(the paper's own text, the text of its reference lists)."""
    spans = reference_spans(text)
    if not spans:
        return text, ""
    body, refs, at = [], [], 0
    for a, b in spans:
        body.append(text[at:a])
        refs.append(text[a:b])
        at = b
    body.append(text[at:])
    return "\n".join(body), "\n".join(refs)


def page_spans(pages: list[str]) -> list[list[tuple[int, int]]]:
    """For each page, the ranges of its own text that are inside a reference list."""
    spans = reference_spans("\n".join(pages))
    out: list[list[tuple[int, int]]] = []
    start = 0
    for text in pages:
        end = start + len(text)
        out.append([(max(a, start) - start, min(b, end) - start) for a, b in spans if a < end and b > start])
        start = end + 1
    return out
