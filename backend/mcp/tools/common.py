"""What the tools share: limits, small helpers, and ``ToolsBase`` (finding items, PDFs, pages and notes)."""

from __future__ import annotations

import difflib
import html
import re
import sqlite3
import time
import unicodedata
from collections import OrderedDict
from pathlib import PurePosixPath

from ...library.fields import ITEM_TYPE_LABELS
from ...library.identifiers import identifiers, norm_arxiv, norm_doi
from ...notes.vault import InvalidPath
from ...pdf.text import extract_pages
from ..protocol import ToolError

AI_FOLDER = "ai"
MAX_PAGES_PER_READ = 10
MAX_CHARS_PER_READ = 80_000
MAX_NOTE_CHARS = 100_000
NOTE_READ_CHARS = 30_000  # a longer note is read by section (get_note shows its outline and sizes instead)
MAX_PAPER_NOTE_CHARS = 20_000
MAX_IMAGES_PER_VIEW = 6


def drop_empty(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def short_authors(authors: str | None, keep: int = 4, include: list[str] | None = None) -> str | None:
    """The first authors; ``include``: names (as searched) to show even when they are in the cut part."""
    if not authors:
        return None
    parts = [a.strip() for a in re.split(r";|\band\b", authors) if a.strip()]
    if len(parts) <= keep + 1:
        return "; ".join(parts)
    hidden = [a for a in parts[keep:] if any(n.casefold() in a.casefold() for n in include or [] if n.strip())]
    more = f"{len(parts)} authors" + (f", incl. {'; '.join(hidden)}" if hidden else "")
    return "; ".join(parts[:keep]) + f"; … ({more})"


_DOI_REF = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?10\.\d{4,9}/\S+$", re.I)
_ARXIV_REF = re.compile(
    r"^(?:arxiv:\s*|https?://arxiv\.org/(?:abs|pdf)/)?(?:\d{4}\.\d{4,5}|[a-z\-]{1,32}(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?(?:\.pdf)?$",
    re.I,
)


def snippet_text(snip: str) -> str:
    """The app's HTML search snippets as plain text, matches in «»."""
    snip = re.sub(r'<span class="hit-src">(.*?)</span>', r"[\1]", snip or "")
    snip = snip.replace("<mark>", "«").replace("</mark>", "»")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", snip)).split())


def _fold(ch: str) -> str:
    s = unicodedata.normalize("NFKD", ch)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def fold_text(text: str) -> tuple[str, list[int]]:
    """Text without case and accents, plus the original index of each folded character
    (so a match in the folded text can be cut from the original)."""
    out, idx = [], []
    for i, ch in enumerate(text):
        f = " " if ch.isspace() else _fold(ch)
        for c in f:
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def parse_pages(spec: str, n: int) -> list[int]:
    """ "3", "2-5", "1,4,7-9", "10-" (to the end) -> sorted page numbers (1-based)."""
    pages: set[int] = set()
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)|(\d+)", part)
        if not m or part == "-":
            raise ToolError(f'Bad page range \'{part}\'. Use e.g. "3", "2-5" or "1,4,7-9".')
        if m.group(3):
            a = b = int(m.group(3))
        else:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else n
        if a < 1 or b < a:
            raise ToolError(f"Bad page range '{part}'.")
        if a > n:
            raise ToolError(f"Page {a} does not exist: the PDF has {n} page{'s' * (n != 1)}.")
        pages.update(range(a, min(b, n) + 1))
    if not pages:
        raise ToolError("No pages given.")
    return sorted(pages)


def page_ranges(pages: list[int]) -> str:
    out, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(str(pages[i]) if i == j else f"{pages[i]}-{pages[j]}")
        i = j + 1
    return ",".join(out)


class ToolsBase:
    """State and lookups the tool families build on: items by key, PDFs and their pages, notes."""

    def __init__(self, services):
        self.s = services
        self._page_cache: OrderedDict[tuple, list[str]] = OrderedDict()
        self._last_sync = 0.0

    # ------------------------------------------------------------------ freshness
    def refresh(self) -> None:
        """Pick up notes edited in the app since the last call (cheap: compares file times)."""
        if time.monotonic() - self._last_sync > 2:
            try:
                self.s.notes.sync()
            finally:
                self._last_sync = time.monotonic()

    # ------------------------------------------------------------------ items
    def _items(self) -> dict[str, dict]:
        out = {}
        for view in ("library", "trash"):
            for p in self.s.papers.list(view):
                p["_trashed"] = view == "trash"
                out[p["id"]] = p
        return out

    def _by_key(self, items: dict[str, dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for p in items.values():  # library items win over trashed ones with the same key
            k = p.get("citation_key")
            if k and (k not in out or out[k]["_trashed"]):
                out[k] = p
        return out

    def _by_identifier(self, ref: str, items: dict[str, dict]) -> dict | None:
        """The item with this DOI or arXiv id (a library item before a trashed one)."""
        if _DOI_REF.match(ref):
            want, index = norm_doi(ref), 0
        elif _ARXIV_REF.match(ref):
            want, index = norm_arxiv(re.sub(r"\.pdf$", "", ref, flags=re.I)), 1
        else:
            return None
        found = [p for p in items.values() if want and identifiers(p)[index] == want]
        return min(found, key=lambda p: bool(p["_trashed"])) if found else None

    def _paper(self, ref: str, items: dict[str, dict] | None = None) -> dict:
        items = items if items is not None else self._items()
        raw = str(ref or "").strip()
        if (hit := self._by_identifier(raw, items)) is not None:
            return hit
        r = re.sub(r"^\[?-?@", "", raw).split(",")[0].rstrip("]").strip()
        if r in items:
            return items[r]
        keys = self._by_key(items)
        if r in keys:
            return keys[r]
        folded = {k.casefold(): p for k, p in keys.items()}
        if r.casefold() in folded:
            return folded[r.casefold()]
        close = self._close_keys(r.casefold(), list(folded))
        hint = f" Did you mean {', '.join(folded[c]['citation_key'] for c in close)}?" if close else ""
        raise ToolError(f"No item with citation key or id '{ref}'.{hint} Use search_library to find it.")

    @staticmethod
    def _close_keys(want: str, keys: list[str]) -> list[str]:
        """Keys that look like ``want``. A key is author + year + word: the same author and year come
        first (the word is the part people misremember), then keys of near spelling."""
        m = re.match(r"([^\W\d_]+)(\d{4})(.*)", want)
        same = []
        if m:
            author, year, word = m.groups()
            same = [k for k in keys if k.startswith(author + year)]
            same.sort(key=lambda k: -difflib.SequenceMatcher(None, word, k[len(author + year) :]).ratio())
        near = difflib.get_close_matches(want, keys, n=3, cutoff=0.6)
        return (same + [k for k in near if k not in same])[:3]

    def _pdfs(self, p: dict) -> list[dict]:
        out = []
        for a in p.get("attachments") or []:
            path = self.s.papers.attachment_path(a)
            if path and path.suffix.lower() == ".pdf":
                out.append(a)
        return out

    def _brief(self, p: dict, authors_like: list[str] | None = None) -> dict:
        doi, arx = identifiers(p)
        return drop_empty(
            {
                "key": p.get("citation_key"),
                "id": None if p.get("citation_key") else p["id"],
                "title": p.get("title"),
                "authors": short_authors(p.get("authors"), include=authors_like),
                "year": p.get("year"),
                "type": ITEM_TYPE_LABELS.get(p.get("item_type") or "", p.get("item_type")),
                "venue": p.get("journal"),
                "doi": doi,
                "arxiv": arx,
                "has_pdf": bool(self._pdfs(p)),
                "in_trash": p.get("_trashed") or None,
            }
        )

    def _label(self, p: dict) -> str:
        return p.get("citation_key") or f"item:{p['id']}"

    # ------------------------------------------------------------------ PDF pages
    def _pages(self, att: dict) -> list[str]:
        path = self.s.papers.attachment_path(att)
        if not path:
            raise ToolError("The PDF file is missing from the library folder.")
        st = path.stat()
        key = (att["id"], st.st_mtime, st.st_size)
        if key in self._page_cache:
            self._page_cache.move_to_end(key)
            return self._page_cache[key]
        pages = extract_pages(path)
        self._page_cache[key] = pages
        while len(self._page_cache) > 16:
            self._page_cache.popitem(last=False)
        return pages

    def _page_count(self, att: dict) -> int | None:
        path = self.s.papers.attachment_path(att)
        if not path:
            return None
        st = path.stat()
        key = (att["id"], st.st_mtime, st.st_size)
        if key in self._page_cache:
            return len(self._page_cache[key])
        try:  # the full-text index already knows it
            with sqlite3.connect(self.s.fulltext.path, timeout=5) as c:
                row = c.execute(
                    "SELECT pages, mtime, size FROM documents WHERE attachment_id = ?", (att["id"],)
                ).fetchone()
            if row and row[1] == st.st_mtime and row[2] == st.st_size and row[0]:
                return row[0]
        except sqlite3.Error:
            pass
        try:
            import pypdf

            return len(pypdf.PdfReader(str(path)).pages)
        except Exception:
            return None

    def _pdf_for(self, p: dict, attachment: int | None) -> dict:
        pdfs = self._pdfs(p)
        if attachment is not None:
            for a in pdfs:
                if a["id"] == attachment:
                    return a
            raise ToolError(
                f"Attachment {attachment} is not a PDF of '{self._label(p)}'. "
                f"Its PDFs: {[a['id'] for a in pdfs] or 'none'}."
            )
        if not pdfs:
            doi, arx = identifiers(p)
            where = ", ".join(x for x in (f"DOI {doi}" if doi else "", f"arXiv {arx}" if arx else "") if x)
            raise ToolError(
                f"'{self._label(p)}' has no PDF in the library."
                + (f" It can be found online via {where}." if where else "")
                + " get_paper shows its abstract."
            )
        return pdfs[0]

    # ------------------------------------------------------------------ notes
    def _note_path(self, ref: str) -> str:
        r = str(ref or "").strip()
        r = re.sub(r"^!?\[\[|\]\]$", "", r).split("|")[0].split("#")[0].strip()
        if not r:
            raise ToolError("Give a note path or title.")
        try:
            rel = self.s.vault.clean_rel(r if r.lower().endswith(".md") else r + ".md")
            if self.s.vault.exists(rel):
                return rel
        except InvalidPath:
            pass
        found = self.s.notes.resolve(r)
        by_title = [n["path"] for n in self.s.notes.list() if (n.get("title") or "").casefold() == r.casefold()]
        found = found + [x for x in sorted(by_title, key=len) if x not in found]  # a file name and a title may differ
        if len(found) > 1:  # never pick one: edit_note would change the wrong note
            raise ToolError(f"'{ref}' names {len(found)} notes; give the path of one: " + "; ".join(found[:8]))
        if found:
            return found[0]
        hints = [f"{h['path']} ({h['title']})" for h in self.s.notes.search(r, limit=5)]
        raise ToolError(
            f"No note '{ref}'."
            + (" Similar: " + "; ".join(hints) if hints else " Use search_library with scope 'notes' to find it.")
        )

    def _link_target(self, path: str) -> str:
        """What to write inside [[…]] to reach ``path``: the file name when that is unambiguous."""
        stem = PurePosixPath(path).stem
        found = self.s.notes.resolve(stem)
        return stem if found and found[0] == path else path[:-3]

    def _note_link(self, path: str, title: str | None = None) -> str:
        target = self._link_target(path)
        if title is None:
            title = (self.s.notes.note(path) or {}).get("title")
        return f"[[{target}|{title}]]" if title and title.casefold() != target.casefold() else f"[[{target}]]"
