"""Extract links, citations, embeds, images, headings and tags from a note.

This is the only code in the app that decides what a note links to. The frontend renders
and autocompletes but never builds the link graph itself.

Syntax (see docs/notes.md):

    [[Note]]  [[Note|alias]]  [[Note#Heading]]  [[folder/Note]]     note links
    ![[Note]]                                                    embeds
    [@key]  [@key, p. 4]  [see @a; @b, ch. 2]  [-@key]             citations (Pandoc)
    ![alt](attachments/x.png)                                     images
    {{task:k7f3q2mx}}                                             tasks (records in library.db)
    ```gallery … ```                                              a gallery of images (not code)
    # Heading                                                     headings (ATX)
    #tag  #nested/tag                                             tags (+ front-matter tags)

Nothing inside fenced code, inline code, math ($…$, $$…$$), HTML comments or the
front-matter counts. A fenced block whose info string is ``gallery`` is not code: its lines are
images and their captions, read like any paragraph (only the fence lines are blanked).
A backslash escapes the next character (``\\[[x]]`` is plain text).

How it works: a "masked" copy of the body is made in which every character that must not
be interpreted (code, math, comments, escaped characters) is replaced by a space, keeping
line breaks and the length intact. The link regexes then run on the masked copy, so
offsets map straight back to the original text for line numbers and context.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from . import frontmatter

CONTEXT_CHARS = 240

# Same characters our citekey generator can produce (citekeys.render), plus a few that
# imported keys commonly use. Trailing ``.`` and ``:`` are punctuation, not part of the key.
_KEY = r"[A-Za-z0-9_][A-Za-z0-9_:.+\-/]*"
_CITE_PART = re.compile(
    r"^(?P<prefix>(?:.*?[\s(])?)(?P<supp>-?)@(?P<key>" + _KEY + r")"
    r"(?:\s*,\s*(?P<loc>.*?))?\s*$",
    re.S,
)
_PAGE = re.compile(r"^(?:p|pp|page|pages|pg)\.?\s*([0-9]+)", re.I)
# A citation can name which PDF of the item it means: [@key, p. 4, att:12] (12 = attachment id).
# Without it, the citation means the item's first PDF.
_ATT = re.compile(r"(?:^|[\s,])att:([0-9]+)(?=$|[\s,])", re.I)
# Any indentation: fences inside list items are common. (Indented code blocks are not
# supported, by the parser and the renderer alike.)
_FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")
# ```gallery, ```gallery|600, ~~~gallery 600: images and captions, not code
_GALLERY = re.compile(r"^gallery(?![^\s|])", re.I)
_TASKS = re.compile(r"^tasks(?:\s|$)", re.I)
_TASK_BLOCK_ID = re.compile(r"^\s*(?:[-*+]\s+)?([a-z0-9]{4,32})\s*$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*(?:#+[ \t]*)?$")
_WIKI = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
_BRACKET = re.compile(r"(?<![!\[\]\\])\[([^\[\]\n]*@[^\[\]\n]*)\](?![(\[\]])")
_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
# A task shown in the note. The id is the task's id in library.db (backend/tasks/store.py).
_TASK = re.compile(r"\{\{task:([a-z0-9]{4,32})\}\}")
_TAG = re.compile(r"(?<![\w/&#\]\)(\[])#([A-Za-z][\w\-/]*)")


@dataclass
class Link:
    kind: str  # "note" | "embed" | "cite" | "image" | "task"
    target: str  # note name, citekey, image path or task id
    line: int  # 1-based line in the whole file
    context: str  # the surrounding line (trimmed), for "cited in …" panels
    heading: str = ""  # [[Note#Heading]]
    alias: str = ""  # [[Note|alias]]  or image alt text
    locator: str = ""  # "p. 4"
    page: int | None = None  # 4  (when the locator names a page)
    prefix: str = ""  # "see"
    suppress_author: bool = False  # [-@key]
    group: int = 0  # citations in the same [...] share a group number

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Heading:
    level: int
    text: str
    line: int


@dataclass
class ParsedNote:
    meta: dict
    title: str
    links: list[Link] = field(default_factory=list)
    headings: list[Heading] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    body: str = ""  # body without front-matter (for full-text search)

    @property
    def citations(self) -> list[Link]:
        return [l for l in self.links if l.kind == "cite"]

    def to_dict(self) -> dict:
        return {
            "meta": self.meta,
            "title": self.title,
            "links": [l.to_dict() for l in self.links],
            "headings": [asdict(h) for h in self.headings],
            "tags": self.tags,
        }


# --------------------------------------------------------------------------- masking
def _blank(chars: list[str], start: int, end: int) -> None:
    for i in range(start, end):
        if chars[i] != "\n":
            chars[i] = " "


def _mask_inline(text: str, chars: list[str], start: int, end: int) -> None:
    """Mask escapes, inline code, math and comments in text[start:end] (one paragraph)."""
    i = start
    while i < end:
        ch = text[i]
        if ch == "\\" and i + 1 < end and text[i + 1] != "\n":
            _blank(chars, i, i + 2)
            i += 2
            continue
        if ch == "`":
            j = i
            while j < end and text[j] == "`":
                j += 1
            run = j - i
            k = j
            close = -1
            while k < end:
                if text[k] == "`":
                    m = k
                    while m < end and text[m] == "`":
                        m += 1
                    if m - k == run:
                        close = k
                        break
                    k = m
                else:
                    k += 1
            if close >= 0:
                _blank(chars, i, close + run)
                i = close + run
            else:
                i = j  # unmatched backticks are literal
            continue
        if ch == "$":
            if text.startswith("$$", i):
                close = text.find("$$", i + 2, end)
                if close >= 0:
                    _blank(chars, i, close + 2)
                    i = close + 2
                    continue
                i += 2
                continue
            # Pandoc rule: opening $ followed by non-space; closing $ preceded by non-space
            # and not followed by a digit. So "$5 and $10" is not math.
            # Inline math must close on the same line, so stray currency signs can never
            # swallow a whole paragraph.
            if i + 1 < end and text[i + 1] not in " \t\n$":
                eol = text.find("\n", i, end)
                line_end = end if eol < 0 else eol
                k = i + 1
                while k < line_end:
                    if text[k] == "\\":
                        k += 2
                        continue
                    if (
                        text[k] == "$"
                        and text[k - 1] not in " \t\n"
                        and not (k + 1 < len(text) and text[k + 1] in "0123456789")
                    ):
                        break
                    k += 1
                if k < line_end:
                    _blank(chars, i, k + 1)
                    i = k + 1
                    continue
            i += 1
            continue
        if text.startswith("<!--", i):
            close = text.find("-->", i + 4)
            stop = close + 3 if close >= 0 else len(text)
            _blank(chars, i, stop)
            i = stop
            continue
        i += 1


def mask(
    body: str,
    galleries: list[tuple[int, int | None, str]] | None = None,
    task_blocks: list[tuple[int, int, str]] | None = None,
) -> str:
    """Return ``body`` with code, math, comments and escaped characters blanked out.

    ``galleries``, when given, receives each ```gallery block as (opening line, closing line or
    None when it is never closed, info string), with 0-based line numbers of ``body``."""
    chars = list(body)
    lines = body.split("\n")
    offsets, pos = [], 0
    for l in lines:
        offsets.append(pos)
        pos += len(l) + 1

    para_start = None  # offset where the current paragraph began
    fence = None  # (char, length) while inside a fenced block
    gallery = None  # (char, length) while inside a ```gallery block
    tasks = None  # (char, length) while inside a ```tasks block
    gallery_open = (0, "")  # where the current gallery began, and its info string
    display_math = False

    def flush(upto: int) -> None:
        nonlocal para_start
        if para_start is not None:
            _mask_inline(body, chars, para_start, upto)
            para_start = None

    for n, line in enumerate(lines):
        start, stop = offsets[n], offsets[n] + len(line)
        if fence:
            _blank(chars, start, stop)
            m = _FENCE.match(line)
            if m and m.group(2)[0] == fence[0] and len(m.group(2)) >= fence[1] and not m.group(3).strip():
                fence = None
            continue
        if gallery:
            m = _FENCE.match(line)
            if m and m.group(2)[0] == gallery[0] and len(m.group(2)) >= gallery[1] and not m.group(3).strip():
                flush(start)
                _blank(chars, start, stop)
                gallery = None
                if galleries is not None:
                    galleries.append((gallery_open[0], n, gallery_open[1]))
                continue
            # inside a gallery every line is text: no code, $$ blocks or nested fences
            if not line.strip():
                flush(start)
            elif para_start is None:
                para_start = start
            continue
        if tasks:
            m = _FENCE.match(line)
            if m and m.group(2)[0] == tasks[0] and len(m.group(2)) >= tasks[1] and not m.group(3).strip():
                _blank(chars, start, stop)
                tasks = None
                continue
            match = _TASK_BLOCK_ID.match(line)
            if match and task_blocks is not None:
                task_blocks.append((n, start + match.start(1), match.group(1)))
            _blank(chars, start, stop)
            continue
        if display_math:
            _blank(chars, start, stop)
            if line.strip().endswith("$$"):
                display_math = False
            continue
        m = _FENCE.match(line)
        if m and not (m.group(2)[0] == "`" and "`" in m.group(3)):
            flush(start)
            if _GALLERY.match(m.group(3).strip()):
                gallery = (m.group(2)[0], len(m.group(2)))
                gallery_open = (n, m.group(3).strip())
            elif _TASKS.match(m.group(3).strip()):
                tasks = (m.group(2)[0], len(m.group(2)))
            else:
                fence = (m.group(2)[0], len(m.group(2)))
            _blank(chars, start, stop)
            continue
        stripped = line.strip()
        if stripped.startswith("$$") and (stripped == "$$" or not stripped[2:].rstrip().endswith("$$")):
            # A $$ block spanning several lines. (Single-line $$x$$ is handled inline.)
            flush(start)
            display_math = True
            _blank(chars, start, stop)
            continue
        if not stripped:
            flush(start)
            continue
        if para_start is None:
            para_start = start
    flush(len(body))
    if gallery and galleries is not None:
        galleries.append((gallery_open[0], None, gallery_open[1]))
    return "".join(chars)


# --------------------------------------------------------------------------- helpers
def _context(lines: list[str], idx: int, col: int) -> str:
    line = lines[idx].strip()
    if len(line) <= CONTEXT_CHARS:
        return line
    raw = lines[idx]
    lead = len(raw) - len(raw.lstrip())
    c = max(0, col - lead)
    a = max(0, c - CONTEXT_CHARS // 2)
    s = line[a : a + CONTEXT_CHARS]
    return ("…" if a > 0 else "") + s + ("…" if a + CONTEXT_CHARS < len(line) else "")


def attachment_of(locator: str | None) -> int | None:
    """The attachment id a citation names with ``att:<id>`` in its locator, or None."""
    m = _ATT.search(locator or "")
    return int(m.group(1)) if m else None


def _reference_definition(masked: str, start: int, end: int) -> bool:
    """``[label]:`` at the start of a line (at most three spaces in) defines a Markdown link
    reference. Anywhere else a colon after the brackets is punctuation: "as in [@key]:"."""
    if not masked.startswith(":", end):
        return False
    line_start = masked.rfind("\n", 0, start) + 1
    return start - line_start <= 3 and not masked[line_start:start].strip(" ")


def _parse_cite_group(inner: str) -> list[dict] | None:
    parts = inner.split(";")
    out = []
    for part in parts:
        m = _CITE_PART.match(part.strip())
        if not m:
            return None  # Pandoc: if one part is not a citation, the bracket isn't either
        key = m.group("key").rstrip(".:/-")
        if not key:
            return None
        loc = (m.group("loc") or "").strip()
        pm = _PAGE.match(loc)
        out.append(
            {
                "key": key,
                "prefix": m.group("prefix").strip(),
                "suppress": m.group("supp") == "-",
                "locator": loc,
                "page": int(pm.group(1)) if pm else None,
            }
        )
    return out


def _split_wiki(inner: str) -> tuple[str, str, str]:
    inner = inner.replace("\\|", "|")  # escaped pipe inside tables
    target, _, alias = inner.partition("|")
    target, _, heading = target.partition("#")
    return target.strip(), heading.strip(), alias.strip()


def _fm_list(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in str(v).split(",") if s.strip()]


def _heading_text(text: str) -> str:
    """A heading without section labels outside its inline code and math."""
    out: list[str] = []
    tick = 0
    math = False
    i = 0
    while i < len(text):
        if not tick and text[i : i + 2] == "\\\\":
            out.append("\\\\")
            i += 2
            continue
        if text[i] == "`":
            j = i
            while j < len(text) and text[j] == "`":
                j += 1
            run = j - i
            tick = run if not tick else (0 if tick == run else tick)
            out.append(text[i:j])
            i = j
            continue
        if not tick and text[i] == "$":
            math = not math
        if not tick and not math and text.startswith("\\label", i):
            m = re.match(r"\\label\s*\{[^{}\n]*\}", text[i:])
            if m:
                i += len(m.group(0))
                continue
        out.append(text[i])
        i += 1
    return "".join(out).rstrip()


# --------------------------------------------------------------------------- main
def parse(text: str, *, fallback_title: str = "") -> ParsedNote:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    header, body, offset = frontmatter.split(text)
    meta = frontmatter.parse(header)
    task_blocks: list[tuple[int, int, str]] = []
    masked = mask(body, task_blocks=task_blocks)
    lines = body.split("\n")
    mlines = masked.split("\n")

    line_starts, pos = [], 0
    for l in mlines:
        line_starts.append(pos)
        pos += len(l) + 1

    def locate(off: int) -> tuple[int, int]:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo, off - line_starts[lo]

    found: list[tuple[int, Link]] = []

    for m in _WIKI.finditer(masked):
        idx, col = locate(m.start())
        target, heading, alias = _split_wiki(body[m.start(2) : m.end(2)])
        if not target and not heading:
            continue
        found.append(
            (
                m.start(),
                Link(
                    kind="embed" if m.group(1) else "note",
                    target=target,
                    heading=heading,
                    alias=alias,
                    line=idx + 1 + offset,
                    context=_context(lines, idx, col),
                ),
            )
        )

    group = 0
    for m in _BRACKET.finditer(masked):
        cites = _parse_cite_group(body[m.start(1) : m.end(1)])
        if not cites or _reference_definition(masked, m.start(), m.end()):
            continue
        group += 1
        idx, col = locate(m.start())
        ctx = _context(lines, idx, col)
        for c in cites:
            found.append(
                (
                    m.start(),
                    Link(
                        kind="cite",
                        target=c["key"],
                        line=idx + 1 + offset,
                        context=ctx,
                        locator=c["locator"],
                        page=c["page"],
                        prefix=c["prefix"],
                        suppress_author=c["suppress"],
                        group=group,
                    ),
                )
            )

    for m in _IMAGE.finditer(masked):
        idx, col = locate(m.start())
        alt = body[m.start(1) : m.end(1)]
        found.append(
            (
                m.start(),
                Link(
                    kind="image",
                    target=body[m.start(2) : m.end(2)],
                    alias=alt.split("|")[0].strip(),
                    line=idx + 1 + offset,
                    context=_context(lines, idx, col),
                ),
            )
        )

    for m in _TASK.finditer(masked):
        idx, col = locate(m.start())
        found.append(
            (
                m.start(),
                Link(kind="task", target=m.group(1), line=idx + 1 + offset, context=_context(lines, idx, col)),
            )
        )

    for idx, at, task_id in task_blocks:
        col = at - line_starts[idx]
        found.append(
            (
                at,
                Link(kind="task", target=task_id, line=idx + 1 + offset, context=_context(lines, idx, col)),
            )
        )

    found.sort(key=lambda t: t[0])

    headings = []
    tags: list[str] = []
    for idx, ml in enumerate(mlines):
        hm = _HEADING.match(ml)
        if hm:
            # Text comes from the original line (inline code/math inside headings is kept).
            raw = _HEADING.match(lines[idx])
            heading = _heading_text((raw or hm).group(2)).strip()
            headings.append(Heading(level=len(hm.group(1)), text=heading, line=idx + 1 + offset))
        for tm in _TAG.finditer(ml):
            t = tm.group(1).rstrip("/-")
            if t and not t.isdigit() and t not in tags:
                tags.append(t)
    for t in _fm_list(meta.get("tags")):
        t = t.lstrip("#")
        if t and t not in tags:
            tags.append(t)

    title = str(meta.get("title") or "").strip()
    if not title:
        h1 = next((h for h in headings if h.level == 1), None)
        title = h1.text if h1 else fallback_title
    return ParsedNote(meta=meta, title=title, links=[l for _, l in found], headings=headings, tags=tags, body=body)


# --------------------------------------------------------------------------- rewriting
def link_spans(text: str) -> list[dict]:
    """Exact positions of citation keys and [[link]] targets in ``text`` (as stored, CRLF kept).

    Each entry: {"kind": "cite"|"note"|"embed", "target": str, "start": int, "end": int} where
    text[start:end] == target. Used to rewrite keys and links without touching anything else
    (code, math, comments and front-matter are skipped exactly as in parse()).
    """
    header, body, _ = frontmatter.split(text)
    base = len(text) - len(body)
    masked = mask(body)
    out = []
    for m in _BRACKET.finditer(masked):
        inner = body[m.start(1) : m.end(1)]
        if _parse_cite_group(inner) is None or _reference_definition(masked, m.start(), m.end()):
            continue
        off = m.start(1)
        for part in inner.split(";"):
            pm = _CITE_PART.match(part.strip())
            assert pm is not None  # _parse_cite_group accepted every part
            lead = len(part) - len(part.lstrip())
            key = pm.group("key").rstrip(".:/-")
            start = base + off + lead + pm.start("key")
            out.append({"kind": "cite", "target": key, "start": start, "end": start + len(key)})
            off += len(part) + 1
    for m in _WIKI.finditer(masked):
        inner = body[m.start(2) : m.end(2)]
        bar = inner.find("|")
        if bar > 0 and inner[bar - 1] == "\\":
            bar -= 1
        cut = len(inner) if bar < 0 else bar
        h = inner.find("#", 0, cut)
        if h >= 0:
            cut = h
        raw = inner[:cut]
        target = raw.strip()
        if not target:
            continue
        start = base + m.start(2) + (len(raw) - len(raw.lstrip()))
        out.append(
            {"kind": "embed" if m.group(1) else "note", "target": target, "start": start, "end": start + len(target)}
        )
    out.sort(key=lambda s: s["start"])
    return out


def replace_spans(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply non-overlapping (start, end, replacement) edits."""
    for start, end, rep in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:start] + rep + text[end:]
    return text
