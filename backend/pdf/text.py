"""PDF text extraction (PyMuPDF when installed, pypdf otherwise)."""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)
ARXIV_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)


def extract_text(path: Path | str, max_pages: int | None = None) -> tuple[str, int]:
    """Return (text, page_count). Never raises; returns ("", 0) on failure."""
    path = str(path)
    try:
        import fitz  # PyMuPDF

        with fitz.open(path) as doc:
            n = doc.page_count
            pages = range(n if max_pages is None else min(n, max_pages))
            return "\n".join(doc[i].get_text() for i in pages), n
    except ImportError:
        pass
    except Exception as e:  # corrupt file etc.
        log.warning("PyMuPDF failed on %s: %s", path, e)
    try:
        import pypdf

        reader = pypdf.PdfReader(path)
        n = len(reader.pages)
        limit = n if max_pages is None else min(n, max_pages)
        parts = []
        for i in range(limit):
            try:
                parts.append(reader.pages[i].extract_text() or "")
            except Exception:
                parts.append("")
        return "\n".join(parts), n
    except Exception as e:
        log.warning("pypdf failed on %s: %s", path, e)
        return "", 0


def extract_pages(path: Path | str) -> list[str]:
    """The text of each page (cleaned), in order. Never raises; returns [] on failure."""
    path = str(path)
    try:
        import fitz  # PyMuPDF

        with fitz.open(path) as doc:
            return [clean_text(doc[i].get_text()) for i in range(doc.page_count)]
    except ImportError:
        pass
    except Exception as e:
        log.warning("PyMuPDF failed on %s: %s", path, e)
    try:
        import pypdf

        out = []
        for page in pypdf.PdfReader(path).pages:
            try:
                out.append(clean_text(page.extract_text() or ""))
            except Exception:
                out.append("")
        return out
    except Exception as e:
        log.warning("pypdf failed on %s: %s", path, e)
        return []


_MATH_FONT = str.maketrans({"ð": "(", "Þ": ")", "¼": "=", "þ": "+", "Ð": "−"})


def _fix_math_font(text: str) -> str:
    """Some journals' PDFs (MathTime fonts) put ( ) = + − at the codes of ð Þ ¼ þ Ð, so a formula reads
    "ε¼0" and "ðt ÞÞ". Undone only where ð and Þ both occur, about equally often: as brackets they pair up."""
    a, b = text.count("ð"), text.count("Þ")
    if min(a, b) >= 3 and abs(a - b) <= max(2, 0.2 * max(a, b)):
        return text.translate(_MATH_FONT)
    return text


def clean_text(text: str) -> str:
    text = _fix_math_font(text)
    text = unicodedata.normalize("NFKC", text.replace("\x00", ""))  # also splits ligatures (ﬀ -> ff)
    text = re.sub(r"-\n(?=[a-z])", "", text)  # re-join hyphenated line breaks
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return text


def find_identifier(text: str) -> dict:
    """Best-effort DOI / arXiv id detection in the first pages of a PDF."""
    m = DOI_RE.search(text or "")
    if m:
        doi = m.group(1).rstrip(".,;)]}")
        return {"doi": doi}
    m = ARXIV_RE.search(text or "")
    if m:
        return {"arxiv": m.group(1)}
    return {}


_ABSTRACT_START = re.compile(r"(?im)^[ \t]*abstract\b[ \t]*[.:—–\-]*[ \t]*")
_ABSTRACT_END = re.compile(
    r"(?im)^[ \t]*(?:key\s*words?|index terms|pacs|keywords?|doi\s*:|subject areas|"
    r"(?:1\.?|i\.?)[ \t]+introduction|introduction)\b"
)
# Physics journals and arXiv print no "Abstract": it follows the line "(Received 20 April 2000)" or
# "(Dated: July 1, 2026)" under the authors. Some PDFs give the brackets as "~" and "!".
_DATE_LINE = re.compile(r"(?im)^[ \t]*[(~]?[ \t]*(?:received|dated)\b[^\n]*$")
ABSTRACT_MAX = 2500
ABSTRACT_MIN = 150
DATE_LINE_WITHIN = 4000  # the date line is under the authors, near the top of the first page


def _first_paragraph(text: str) -> str:
    """The lines of ``text`` up to where a paragraph set in a narrower column starts: an abstract runs across
    the page, the body of a letter below it in two columns, so its lines are clearly shorter. Without such a
    change, all of ``text``."""
    lines = text.split("\n")
    for i in range(3, len(lines) - 4):
        if not lines[i - 1].rstrip().endswith((".", ")", "]")):
            continue
        before = sorted(len(l) for l in lines[:i] if len(l) > 20)
        after = [len(l) for l in lines[i : i + 4]]
        if before and max(after) < 0.72 * before[len(before) // 2]:
            return "\n".join(lines[:i])
    return text


def abstract_from_text(text: str) -> str | None:
    """The abstract printed on a paper's first page: the text after a line starting "Abstract", or else
    after the "(Received …)" / "(Dated: …)" line under the authors, up to "Keywords", "PACS" or the
    introduction (for a letter with no such heading, up to where the two-column text starts). None when
    neither line is there or too little follows it. Approximate."""
    m = _ABSTRACT_START.search(text)
    if m:
        rest = text[m.end() :]
        end = _ABSTRACT_END.search(rest)
        body = rest[: end.start()] if end else rest
    else:
        m = _DATE_LINE.search(text[:DATE_LINE_WITHIN])
        if not m:
            return None
        rest = text[m.end() :].lstrip("\n")
        if not re.search(r"[)!]", m.group(0)):  # the dates run over two or three lines
            close = re.search(r"[)!][ \t]*\n", rest[:400])
            rest = rest[close.end() :] if close else rest
        if re.match(r"\W*(?:accepted|revised|published|cite this)\b", rest, re.I):
            return None  # a journal's box of dates, not the line under the authors
        end = _ABSTRACT_END.search(rest)
        body = (
            rest[: end.start()]
            if end and end.start() <= ABSTRACT_MAX + 200
            else _first_paragraph(rest[: ABSTRACT_MAX + 200])
        )
    body = " ".join(re.sub(r"-\n(?=[a-z])", "", body[: ABSTRACT_MAX + 200]).split())
    if len(body) < ABSTRACT_MIN:
        return None
    if len(body) > ABSTRACT_MAX:
        body = body[:ABSTRACT_MAX].rsplit(" ", 1)[0] + "…"
    return body
