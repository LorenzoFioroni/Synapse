"""Checks a note before the MCP server saves it, so what Claude writes always works in the app.

* ``[[links]]`` must resolve. A link written with a note's *title* (links resolve by file
  name) is fixed when exactly one note has that title: ``[[Title]]`` → ``[[stem|Title]]``.
  ``[[Note#Heading]]`` must name an existing heading.
* ``[@key]`` must be a library item; ``p. N`` must exist in its PDF.
* A citation inside a block quote marks a *quote* (as "Quote to note" writes it): the quoted
  words must be on the cited page, matched like the viewer does (letters and digits only),
  or the citation could not open the PDF at them.
* Images must exist in the notes folder.

Problems are returned as errors (nothing is saved); things that still work but look wrong
(a citation of an item in the trash) as warnings. Math written with Unicode symbols
outside ``$…$`` is an error too: notes render LaTeX with KaTeX.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

from ..notes import frontmatter, parser

CITES = re.compile(r"\[[^\[\]]*@[^\[\]]*\]")
QUOTE_LINE = re.compile(r"^\s{0,3}>")
HEAD = 40  # as frontend/src/viewer/locate.ts
LETTERS = {"æ": "ae", "œ": "oe", "ß": "ss", "ø": "o", "ł": "l", "đ": "d", "ı": "i"}
MATHY = re.compile(r"[≈≲≳∼≃≅≡≠≤≥≪≫∑∏∫∮√∞∝⟨⟩⊗⊕†∂∇∈∉⊂⊆∀∃±∓×ℏ₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ̄̂̃]")


def normalise(s: str) -> str:
    """Lower-case letters and digits only (the viewer's rule for finding quotes)."""
    out = []
    for ch in s:
        low = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)).lower()
        for c in LETTERS.get(low, low):
            if c.isalnum():
                out.append(c)
    return "".join(out)


def quote_text(s: str) -> str:
    """As citeTarget.js quoteText: the quoted words without citations or emphasis, one line."""
    s = CITES.sub(" ", s)
    s = re.sub(r"\(\s*\)", " ", s)
    s = re.sub(r"[*_`]+", "", s)
    return " ".join(s.split())[:400]


def found_on(page_text: str, quote: str) -> bool:
    q = normalise(quote)
    if len(q) < 4:
        return True
    t = normalise(page_text)
    if q in t:
        return True
    return len(q) >= HEAD and (q[:HEAD] in t or q[-HEAD:] in t)


def slug_heading(text: str) -> str:
    """As render.js slugHeading."""
    s = "".join(c for c in str(text).lower() if c.isalnum() or c.isspace() or c == "-")
    return "-".join(s.split())


class NoteChecker:
    """Uses the MCP tools object for library lookups (keys, PDFs, page text)."""

    def __init__(self, tools):
        self.t = tools
        self.s = tools.s
        self.fixes: list[str] = []  # links rewritten from a title to the file name

    def check(self, text: str, path: str) -> tuple[str, list[str], list[str]]:
        """Returns (text with title links fixed, errors, warnings)."""
        errors: list[str] = []
        warnings: list[str] = []
        text = self._fix_links(text, path, errors)
        parsed = parser.parse(text)
        items = self.t._items()
        keys = self.t._by_key(items)
        self._check_cites(parsed, keys, errors, warnings)
        self._check_quotes(text, keys, errors)
        self._check_images(parsed, errors)
        self._check_math(text, errors)
        return text, errors, warnings

    # ------------------------------------------------------------------ links
    def _fix_links(self, text: str, path: str, errors: list[str]) -> str:
        header, body, _ = frontmatter.split(text)
        base = len(text) - len(body)
        masked = parser.mask(body)
        notes = self.s.notes.list()
        by_title: dict[str, list[str]] = {}
        for n in notes:
            by_title.setdefault((n.get("title") or "").strip().casefold(), []).append(n["path"])
        own_headings = [h.text for h in parser.parse(text).headings]
        edits = []
        for m in parser._WIKI.finditer(masked):
            inner = body[m.start(2) : m.end(2)]
            target, rest = inner, ""
            cut = len(inner)
            for sep in ("|", "#"):
                i = inner.find(sep)
                if 0 <= i < cut:
                    cut = i
            target, rest = inner[:cut].strip(), inner[cut:]
            heading = ""
            if rest.startswith("#"):
                heading = rest[1:].split("|", 1)[0].strip()
            alias = rest.split("|", 1)[1].strip() if "|" in rest else ""
            line = text[: base + m.start()].count("\n") + 1
            if not target:  # [[#Heading]] in this note
                if heading and slug_heading(heading) not in {slug_heading(h) for h in own_headings}:
                    errors.append(f"line {line}: [[#{heading}]]: this note has no such heading.")
                continue
            found = self.s.notes.resolve(target, from_path=path)
            resolved = found[0] if found else None
            if not resolved:
                titled = by_title.get(target.casefold(), [])
                if len(titled) == 1:
                    resolved = titled[0]
                    stem = self.t._link_target(resolved)
                    new = stem + (f"#{heading}" if heading else "") + "|" + (alias or target)
                    edits.append((base + m.start(2), base + m.end(2), new))
                    self.fixes.append(f"[[{inner}]] → [[{new}]]")
                else:
                    hint = self._similar_notes(target)
                    errors.append(
                        f"line {line}: [[{target}]] does not match any note. Links use the file name "
                        f"(the `link` value the tools give for each note)." + (f" Did you mean {hint}?" if hint else "")
                    )
                    continue
            if heading:
                info = self.s.notes.note(resolved) or {}
                hs = [h["text"] for h in info.get("headings") or []]
                if slug_heading(heading) not in {slug_heading(h) for h in hs}:
                    errors.append(
                        f"line {line}: [[{target}#{heading}]]: no such heading in {resolved}. "
                        f"Its headings: {', '.join(hs) or 'none'}."
                    )
        return parser.replace_spans(text, edits) if edits else text

    def _similar_notes(self, target: str) -> str:
        cands = {}
        for n in self.s.notes.list():
            cands[(n.get("title") or "").casefold()] = n["path"]
            cands[n["path"].rsplit("/", 1)[-1][:-3].casefold()] = n["path"]
        close = difflib.get_close_matches(target.casefold(), list(cands), n=3, cutoff=0.5)
        links = []
        for c in close:
            l = self.t._note_link(cands[c])
            if l not in links:
                links.append(l)
        return " or ".join(links)

    # ------------------------------------------------------------------ citations
    def _check_cites(self, parsed, keys, errors, warnings) -> None:
        for l in parsed.links:
            if l.kind != "cite":
                continue
            p = keys.get(l.target)
            if not p:
                close = difflib.get_close_matches(l.target, list(keys), n=3, cutoff=0.6)
                errors.append(
                    f"line {l.line}: [@{l.target}] is not in the library."
                    + (
                        f" Did you mean {', '.join('@' + c for c in close)}?"
                        if close
                        else " Only library items can be cited; mention other works in plain text (with their DOI)."
                    )
                )
                continue
            if p.get("_trashed"):
                warnings.append(f"line {l.line}: [@{l.target}] is in the library's trash.")
            if l.page:
                pdfs = self.t._pdfs(p)
                n = self.t._page_count(pdfs[0]) if pdfs else None
                if n and l.page > n:
                    errors.append(f"line {l.line}: [@{l.target}, p. {l.page}]: its PDF has only {n} pages.")

    def _check_quotes(self, text: str, keys, errors) -> None:
        _, body, start_line = frontmatter.split(text)
        masked = parser.mask(body).split("\n")
        lines = body.split("\n")
        n = 0
        while n < len(lines):
            if not QUOTE_LINE.match(masked[n]) or not QUOTE_LINE.match(lines[n]):
                n += 1
                continue
            a = n
            while n + 1 < len(lines) and QUOTE_LINE.match(lines[n + 1]):
                n += 1
            block = [re.sub(r"^\s{0,3}(?:>\s?)+", "", l) for l in lines[a : n + 1]]
            block = [l for l in block if not re.match(r"^\[![a-z]+\]", l, re.I)]
            n += 1
            cites = [c for c in parser.parse("\n".join(block)).links if c.kind == "cite"]
            if not cites:
                continue
            quote = quote_text(" ".join(block))
            line = start_line + a + 1
            if not quote:
                continue
            for c in cites:
                p = keys.get(c.target)
                if not p or not self.t._pdfs(p):
                    continue  # unknown key: reported already; no PDF: nothing to check against
                where = f"line {line}: the block quote citing [@{c.target}{', ' + c.locator if c.locator else ''}]"
                why = (
                    " (A citation inside a block quote or callout marks a quote: the app looks for the quoted "
                    "words on that page. Quote the PDF's words exactly, or put the citation outside the quote.)"
                )
                if not c.page:
                    errors.append(f"{where} needs a page: [@{c.target}, p. N].{why}")
                    continue
                try:
                    pages = self.t._pages(self.t._pdfs(p)[0])
                except Exception:
                    continue
                if c.page > len(pages) or found_on(pages[c.page - 1], quote):
                    continue
                elsewhere = [i for i, t in enumerate(pages, 1) if found_on(t, quote)]
                if elsewhere:
                    errors.append(f"{where}: these words are on page {elsewhere[0]}, not {c.page}.")
                else:
                    errors.append(
                        f"{where}: the quoted words were not found on page {c.page} of its PDF."
                        f" Copy them from read_paper (prose only; formulas in a PDF's text rarely match).{why}"
                    )

    # ------------------------------------------------------------------ images, math
    def _check_images(self, parsed, errors) -> None:
        for l in parsed.links:
            if l.kind != "image" or re.match(r"^(https?:)?//|^data:", l.target):
                continue
            try:
                p = self.s.vault.root / self.s.vault.clean_rel(l.target, suffix=None)
            except Exception:
                p = None
            if not p or not p.is_file():
                errors.append(f"line {l.line}: image {l.target} does not exist. Use the markdown snapshot returns.")

    def _check_math(self, text: str, errors) -> None:
        """Formulas written with Unicode symbols outside math (block quotes are the PDF's own words)."""
        _, body, start_line = frontmatter.split(text)
        masked = parser.mask(body).split("\n")
        bad = [start_line + i + 1 for i, l in enumerate(masked) if MATHY.search(l) and not QUOTE_LINE.match(l)]
        if bad:
            shown = ", ".join(map(str, bad[:8])) + ("…" if len(bad) > 8 else "")
            errors.append(
                f"{'line' if len(bad) == 1 else 'lines'} {shown}: math is written with Unicode symbols (≈, ⟨⟩, subscripts…) outside $…$. "
                f"Notes render LaTeX with KaTeX: write $t \\lesssim N - 1.6$ inline and $$…$$ for display."
            )
