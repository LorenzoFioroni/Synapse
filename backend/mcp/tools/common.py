"""What the tools share: limits, small helpers, and ``ToolsBase`` (finding items, PDFs, pages and notes)."""

from __future__ import annotations

import difflib
import html
import re
import time
import unicodedata
from pathlib import PurePosixPath

from ...library.fields import ITEM_TYPE_LABELS, first_author
from ...library.identifiers import ARXIV_DOI, ARXIV_ID, identifiers, norm_arxiv, norm_doi
from ...notes.vault import InvalidPath, is_pdf_path
from ...pdf.source import PdfSource
from ...pdf.text import PageTextCache
from ..protocol import ToolError

AI_FOLDER = "ai"
MAX_PAGES_PER_READ = 10
MAX_CHARS_PER_READ = 40_000  # AI apps refuse a tool result of about 25,000 tokens (protocol.MAX_RESULT_CHARS)
MAX_CHARS_PER_PAGE = 25_000  # one PDF page as read_paper returns it; a page is normally 3,000-6,000
MAX_LISTED_CHARS = 35_000  # a list of works (references): the rest is reached with offset
MAX_NOTE_CHARS = 100_000
NOTE_READ_CHARS = 30_000  # a longer note is read by section (get_note shows its outline and sizes instead)
MAX_PAPER_NOTE_CHARS = 20_000
MAX_IMAGES_PER_VIEW = 6
PAGE_CACHE_SIZE = 16  # PDFs whose page texts are kept
NEAR_CHARS = 300  # the words of a search "together": all of them within this many characters


def page_for_reading(text: str, limit: int = MAX_CHARS_PER_PAGE) -> tuple[str, str]:
    """One PDF page's text for read_paper, and what was done to it ("" if nothing). A page that reproduces
    another one (a figure taken from a journal) repeats whole paragraphs: a line of 30 characters or more
    is kept once. What is still too long loses its middle."""
    seen: set[str] = set()
    kept: list[str] = []
    for line in text.split("\n"):
        key = line.strip()
        if len(key) >= 30:
            if key in seen:
                continue
            seen.add(key)
        kept.append(line)
    repeated = text.count("\n") + 1 - len(kept)
    out = "\n".join(kept)
    notes = [f"{repeated} repeated lines left out"] if repeated else []
    if len(out) > limit:
        head = limit * 2 // 3
        notes.append(f"{len(out) - limit} characters cut from the middle: the page is too long to give whole")
        out = f"{out[:head]}\n[… cut …]\n{out[len(out) - (limit - head) :]}"
    return out, "; ".join(notes)


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
    r"^(?:arxiv:\s*|https?://arxiv\.org/(?:abs|pdf)/)?" + ARXIV_ID.pattern + r"(?:\.pdf)?$",
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


def together(hits: dict[str, list[int]], matched: list[str]) -> list[int]:
    """Positions of the rarest word where every other matched word is also within NEAR_CHARS."""
    rare = min(matched, key=lambda t: len(hits[t]))
    return [
        pos for pos in hits[rare] if all(any(abs(q - pos) <= NEAR_CHARS for q in hits[t]) for t in matched if t != rare)
    ]


def sentence(text: str) -> str:
    """A message as a sentence that ends in a full stop, to be followed by another."""
    text = (text or "").strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def check_page(page: int, n: int) -> None:
    """Refuse a page number outside a PDF of ``n`` pages."""
    if not 1 <= page <= n:
        raise ToolError(f"Page {page} does not exist: the PDF has {n} page{'s' * (n != 1)}.")


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
        check_page(a, n)
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


def _same_work(have: tuple[str | None, str | None], doi: str | None, arx: str | None) -> bool:
    return bool(doi and have[0] == doi or arx and have[1] == arx)


class ToolsBase:
    """State and lookups the tool families build on: items by key, PDFs and their pages, notes."""

    def __init__(self, services):
        self.s = services
        self._page_cache = PageTextCache(PAGE_CACHE_SIZE)
        self._last_sync = 0.0

    # ------------------------------------------------------------------ freshness
    def refresh(self) -> None:
        """Pick up notes edited in the app since the last call (cheap: compares file times)."""
        if time.monotonic() - self._last_sync > 2:
            try:
                self.s.notes.sync()
                self.s.note_pdfs.sync()
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
            doi = norm_doi(ref)
            m = ARXIV_DOI.match(doi or "")
            arx = norm_arxiv(m.group(1)) if m else None  # an arXiv DOI also names items that store only the id
        elif _ARXIV_REF.match(ref):
            doi, arx = None, norm_arxiv(re.sub(r"\.pdf$", "", ref, flags=re.I))
        else:
            return None
        found = [p for p in items.values() if (doi or arx) and _same_work(identifiers(p), doi, arx)]
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
        if is_pdf_path(r) and not _ARXIV_REF.match(raw):
            raise ToolError(
                f"'{ref}' is a PDF in the notes folders, not a library item. Only the PDF tools take it, by its "
                "path: read_paper, search_in_paper, get_paper_outline, view_page, get_annotations and add_highlight."
            )
        close = self._close_keys(r.casefold(), list(folded))
        hint = f" Did you mean {', '.join(folded[c]['citation_key'] for c in close)}?" if close else ""
        raise ToolError(f"No item with citation key or id '{ref}'.{hint} Use search_library to find it.")

    @staticmethod
    def _close_keys(want: str, keys: list[str]) -> list[str]:
        """Keys that look like ``want``. A key is author + year + word: the same author and year come
        first (the word is the part people misremember), then the same author in the nearest years (the
        year is the next thing to go wrong), then keys of near spelling."""
        m = re.match(r"([^\W\d_]+)(\d{4})?(.*)", want)
        same: list[str] = []
        if m and m.group(1):
            author, year, word = m.group(1), m.group(2), m.group(3)
            likeness = lambda k: -difflib.SequenceMatcher(None, word, re.sub(r"^\D+\d{4}", "", k)).ratio()
            theirs = [k for k in keys if re.fullmatch(re.escape(author) + r"\d{4}.*", k)]
            if year:
                same = sorted((k for k in theirs if k.startswith(author + year)), key=likeness)
                years = lambda k: abs(int(k[len(author) : len(author) + 4]) - int(year))
                same += sorted((k for k in theirs if k not in same), key=lambda k: (years(k), likeness(k)))
            else:
                same = sorted(theirs, key=likeness)
        near = difflib.get_close_matches(want, keys, n=3, cutoff=0.6)
        return (same + [k for k in near if k not in same])[:3]

    def _pdfs(self, p: dict) -> list[dict]:
        out = []
        for a in p.get("attachments") or []:
            path = self.s.papers.attachment_path(a)
            if path and is_pdf_path(path.name):
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

    @staticmethod
    def _one_line(p: dict, added: bool = False) -> str:
        """An item on one line (key, year, first author, title): for long lists."""
        key = p.get("citation_key") or f"item:{p['id']}"
        when = f" [added {(p.get('date_added') or '')[:10]}]" if added and p.get("date_added") else ""
        return (
            f"{key} ({p.get('year') or 'n.d.'}) {first_author(p.get('authors'))}: {p.get('title') or ''}{when}".replace(
                "  ", " "
            )
        )

    # ------------------------------------------------------------------ PDF pages
    def _src(self, pdf: dict | PdfSource, p: dict | None = None) -> PdfSource:
        """An attachment of a library item (with the item) as a PdfSource; a PdfSource as it is."""
        if isinstance(pdf, PdfSource):
            return pdf
        path = self.s.papers.attachment_path(pdf)
        if not path:
            raise ToolError("The PDF file is missing from the library folder.")
        key = (p or {}).get("citation_key") or "?"
        return PdfSource(
            f"att:{pdf['id']}",
            path,
            self._label(p) if p else str(pdf["id"]),
            (p or {}).get("title") or "",
            f"[@{key}, p. {{page}}]",
            attachment_id=pdf["id"],
        )

    def _source(self, paper: str, attachment: int | None = None) -> PdfSource:
        """The PDF a tool call names: an item's (``paper`` a citation key, id, DOI …; ``attachment``
        picks one of several), or a PDF in the notes folders (``paper`` its path or file name,
        ``folder/file.pdf``, ``[[file.pdf#p=4]]``)."""
        ref = re.sub(r"^!?\[\[|\]\]$", "", str(paper or "").strip()).split("|")[0].split("#")[0].strip()
        if is_pdf_path(ref):
            if attachment is not None:
                raise ToolError("attachment is for the PDFs of a library item; a PDF in the notes folders has none.")
            return self._notes_source(ref)
        p = self._paper(paper)
        return self._src(self._pdf_for(p, attachment), p)

    def _notes_source(self, ref: str) -> PdfSource:
        try:
            rel = self.s.vault.clean_rel(ref, suffix=".pdf")
        except InvalidPath:
            rel = ref
        row = self.s.note_pdfs.by_path(rel)
        if not row:
            found = [x for x in self.s.notes.resolve_pdf(ref) if self.s.note_pdfs.by_path(x)]
            if len(found) > 1:  # never pick one: a highlight would land on the wrong PDF
                raise ToolError(f"'{ref}' names {len(found)} PDFs; give the path of one: " + "; ".join(found[:8]))
            row = self.s.note_pdfs.by_path(found[0]) if found else None
        if not row:
            raise ToolError(
                f"No PDF '{ref}' in the notes folders. Use its path, e.g. 'refs/paper.pdf' "
                "(get_note lists the PDFs a note links to)."
            )
        got = self.s.note_pdfs.file_of(row["id"])
        if not got:
            raise ToolError(f"The PDF '{row['path']}' is not in the notes folders any more.")
        return PdfSource(
            f"npdf:{row['id']}",
            got[1],
            row["path"],
            row["name"],
            f"[[{row['path']}#p={{page}}]]",
            note_pdf_id=row["id"],
        )

    def _pages(self, pdf: dict | PdfSource) -> list[str]:
        src = self._src(pdf)
        return self._page_cache.pages(src.key, src.path)

    def _page_count(self, pdf: dict | PdfSource) -> int | None:
        try:
            src = self._src(pdf)
        except ToolError:
            return None
        cached = self._page_cache.peek(src.key, src.path)
        if cached is not None:
            return len(cached)
        # the full-text index may already know it
        if n := self.s.fulltext.indexed_pages(src.path, attachment_id=src.attachment_id, pdf_id=src.note_pdf_id):
            return n
        try:
            import pypdf

            return len(pypdf.PdfReader(str(src.path)).pages)
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
