"""Tools for papers and their PDFs: get_paper, get_paper_outline, search_in_paper, read_paper, view_page, snapshot."""

from __future__ import annotations

import re

from ...library import search as search_mod
from ...library.fields import FIELDS, ITEM_TYPE_LABELS
from ...library.identifiers import identifiers
from ...library.repository import NotFound
from ...pdf import outline as pdfoutline
from ...pdf import regions as pdfregions
from ...pdf import render as pdfrender
from ...pdf.text import abstract_from_text, clean_text, extract_text
from ..protocol import Image, Parts, ToolError
from .common import (
    MAX_CHARS_PER_READ,
    MAX_PAGES_PER_READ,
    MAX_PAPER_NOTE_CHARS,
    ToolsBase,
    drop_empty,
    fold_text,
    page_ranges,
    parse_pages,
)

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f]")  # pdfium marks a line-end hyphen with U+0002
NEAR_CHARS = 300  # the words of a search "together": all of them within this many characters


def _together(hits: dict[str, list[int]], matched: list[str]) -> list[int]:
    """Positions of the rarest word where every other matched word is also within NEAR_CHARS."""
    rare = min(matched, key=lambda t: len(hits[t]))
    return [
        pos for pos in hits[rare] if all(any(abs(q - pos) <= NEAR_CHARS for q in hits[t]) for t in matched if t != rare)
    ]


class PaperToolsMixin(ToolsBase):
    """get_paper, get_paper_outline, search_in_paper, read_paper, view_page, snapshot."""

    MAX_PAPERS_AT_ONCE = 20
    MAX_CITING_NOTES = 10  # a much-cited paper: the rest is a count

    def _paper_summary(self, p: dict) -> dict:
        """What get_paper gives for each item asked for in a batch: metadata and the whole abstract."""
        return drop_empty(
            {
                **self._brief(p),
                "abstract": " ".join(str(p.get("abstract") or "").split()) or None,
                **({} if p.get("abstract") else self._pdf_abstract(p)),
                "tags": p.get("tags"),
            }
        )

    def _pdf_abstract(self, p: dict) -> dict:
        """For an item with no stored abstract: the one printed on its PDF's first page when it can be
        recognised (marked approximate), else a pointer to read_paper."""
        found = None
        for att in self._pdfs(p)[:1]:
            path = self.s.papers.attachment_path(att)
            if path:
                found = abstract_from_text(clean_text(extract_text(path, max_pages=1)[0]))
        if found:
            return {
                "abstract": found,
                "abstract_from_pdf": "none stored: this is the abstract printed on page 1 of the PDF (approximate)",
            }
        return {"no_abstract": "none stored" + ("; read_paper page 1 has the PDF's own" if self._pdfs(p) else "")}

    def get_paper(self, paper: str = "", papers: list | None = None):
        items = self._items()
        if papers:
            rows = []
            for ref in papers[: self.MAX_PAPERS_AT_ONCE]:
                try:
                    rows.append(self._paper_summary(self._paper(ref, items)))
                except ToolError as e:
                    rows.append({"ref": str(ref), "error": str(e)})
            return drop_empty(
                {
                    "papers": rows,
                    "not_read": [str(r) for r in papers[self.MAX_PAPERS_AT_ONCE :]] or None,
                    "hint": "A summary per item; get_paper(paper=…) adds notes, references and plugin fields.",
                }
            )
        if not paper:
            raise ToolError("Give paper (one citation key, id, DOI or arXiv id) or papers (a list of them).")
        p = self._paper(paper, items)
        skip = {"date_modified", "citation_key", "item_type", "doi"}
        meta = {f.key: p.get(f.key) for f in FIELDS if f.key not in skip}
        doi, arx = identifiers(p)
        paths = self.s.folders.paths()
        key = p.get("citation_key") or ""
        out = drop_empty(
            {
                "key": key or None,
                "id": p["id"],
                "type": ITEM_TYPE_LABELS.get(p.get("item_type") or "", p.get("item_type")),
                "doi": doi,
                "arxiv": arx,
                **drop_empty(meta),
                "tags": p.get("tags"),
                "folders": ["/".join(paths.get(f, [])) for f in p.get("folder_ids") or []],
                "in_trash": p.get("_trashed") or None,
                "pdfs": [
                    drop_empty({"attachment": a["id"], "name": a.get("name"), "pages": self._page_count(a)})
                    for a in self._pdfs(p)
                ],
            }
        )
        if not p.get("abstract"):
            out.update(self._pdf_abstract(p))
        note_text = None
        if key:
            pnotes = self.s.notes.paper_notes(key)
            if pnotes:
                note_path = pnotes[0]["path"]
                out["paper_note"] = note_path
                out["paper_note_link"] = self._note_link(note_path, pnotes[0].get("title"))
                try:
                    note_text = self.s.vault.read(note_path)[0]
                except Exception:
                    note_text = None
            mine = {n["path"] for n in pnotes}
            per_note: dict[str, dict] = {}  # one entry per note: every line citing the paper, one context
            for c in self.s.notes.citing(key):
                if c["path"] in mine:
                    continue
                entry = per_note.setdefault(
                    c["path"],
                    drop_empty(
                        {
                            "path": c["path"],
                            "title": c.get("source_title"),
                            "link": self._note_link(c["path"], c.get("source_title")),
                            "locator": c.get("locator"),
                            "context": c.get("context"),
                        }
                    ),
                )
                lines = entry.setdefault("lines", [])
                if c["line"] not in lines:  # a line citing it twice shows once
                    lines.append(c["line"])
            cited_in = list(per_note.values())
            out["cited_in_notes"] = cited_in[: self.MAX_CITING_NOTES]
            if len(cited_in) > self.MAX_CITING_NOTES:
                out["cited_in_notes_total"] = len(cited_in)
        plugin_fields = self.s.plugins.fields.labelled(p)  # values plugins attach (citation counts …)
        if plugin_fields:
            out["plugin_fields"] = plugin_fields
        try:
            view = self.s.citations.item_view(p["id"])
        except NotFound:
            view = None
        if view:
            f = view["fetch"] or {}
            out["references"] = drop_empty(
                {
                    "stored": len(view["references"]),
                    "in_library": sum(1 for r in view["references"] if r.get("library_id")),
                    "source": f.get("source"),
                    "fetched_at": f.get("fetched_at"),
                    "error": f.get("error"),
                    "never_fetched": True if not f else None,
                }
            )
            out["cited_by_in_library"] = [self._label(items[c["id"]]) for c in view["cited_by"] if c["id"] in items]
        if note_text is None:
            return drop_empty(out)
        if len(note_text) > MAX_PAPER_NOTE_CHARS:
            note_text = note_text[:MAX_PAPER_NOTE_CHARS] + "\n\n[… cut; read the rest with get_note]"
        return Parts([drop_empty(out), f"The user's paper note ({out['paper_note']}):\n\n{note_text}"])

    def get_paper_outline(self, paper: str, attachment: int | None = None, captions: bool = True):
        p = self._paper(paper)
        att = self._pdf_for(p, attachment)
        path = self.s.papers.attachment_path(att)
        pages = self._pages(att)
        entries, source, matched = pdfoutline.bookmarks(path), "the PDF's bookmarks", True
        if not entries:
            entries, matched = pdfoutline.contents_page_outline(pages)
            source = "the printed table of contents"
        out: dict = {"paper": self._label(p), "title": p.get("title"), "pages_total": len(pages)}
        completed = False
        if len(entries) < pdfoutline.SPARSE:  # a letter: its bookmarks miss the run-in section headings
            entries, completed = pdfoutline.with_text_headings(entries, pages)
        if entries:
            out["outline_from"] = source + (
                " plus headings recognised in the page text (approximate)" if completed else ""
            )
            out["outline"] = [f"{'  ' * (e['level'] - 1)}{e['title']} (p. {e['page']})" for e in entries]
            if not matched:
                out["warning"] = (
                    "The page numbers are the printed ones and could not be matched to PDF pages: "
                    "check with search_in_paper before relying on them."
                )
        else:
            out["outline_note"] = (
                "No bookmarks or table of contents were found. Use search_in_paper for a heading's words, "
                "or read_paper on the first pages."
            )
        if captions:
            found = pdfoutline.captions(pages)
            if found:
                out["figures_and_tables"] = found
        return out

    def search_in_paper(self, paper: str, query: str, max_results: int = 8, attachment: int | None = None):
        p = self._paper(paper)
        att = self._pdf_for(p, attachment)
        pages = self._pages(att)
        if not any(t.strip() for t in pages):
            raise ToolError(f"No text could be extracted from the PDF of '{self._label(p)}' (a scan?).")
        terms = [t for t, neg, f in search_mod.parse_query(query) if not neg]
        terms = [search_mod.normalize(t) for t in terms if search_mod.normalize(t)]
        if not terms:
            raise ToolError("Give some words to search for.")
        phrase = search_mod.normalize(query.replace('"', "")) if len(terms) > 1 else None
        scored = []
        for n, text in enumerate(pages, 1):
            folded, idx = fold_text(text)
            hits = {t: [m.start() for m in re.finditer(re.escape(t), folded)] for t in terms}
            matched = [t for t in terms if hits[t]]
            if not matched:
                continue
            phrase_hits = [m.start() for m in re.finditer(re.escape(phrase), folded)] if phrase else []
            total = sum(len(v) for v in hits.values())
            near = _together(hits, matched) if len(matched) > 1 else []
            scored.append(
                ((len(matched), len(phrase_hits), len(near), total), n, matched, phrase_hits, near, hits, text, idx)
            )
        scored.sort(key=lambda s: (s[0], -s[1]), reverse=True)
        sections = pdfoutline.bookmarks(self.s.papers.attachment_path(att)) if scored else []
        results = []
        for score, n, matched, phrase_hits, near, hits, text, idx in scored[:max_results]:
            starts = phrase_hits[:1] + near[:1] + [hits[t][0] for t in matched]
            snippets: list[str] = []
            used: list[int] = []
            for st in starts:
                pos = idx[min(st, len(idx) - 1)]
                if any(abs(pos - u) < 200 for u in used):
                    continue
                used.append(pos)
                a, b = max(0, pos - 160), min(len(text), pos + 220)
                snippets.append(
                    ("…" if a else "") + " ".join(_CONTROL.sub("", text[a:b]).split()) + ("…" if b < len(text) else "")
                )
                if len(snippets) == 3:
                    break
            where = next((e["title"] for e in reversed(sections) if e["page"] <= n), None)
            results.append(
                drop_empty(
                    {
                        "page": n,
                        "section": where,
                        "matched": matched,
                        "together": "all the words within a few lines of each other" if near else None,
                        "occurrences": score[3],
                        "snippets": snippets,
                    }
                )
            )
        out = {
            "paper": self._label(p),
            "title": p.get("title"),
            "attachment": att["id"],
            "pages_total": len(pages),
            "pages_matching": len(scored),
            "results": results,
        }
        if not results:
            out["hint"] = "No page contains these words. Try other words or synonyms."
        return out

    def read_paper(self, paper: str, pages: str = "1-2", attachment: int | None = None):
        p = self._paper(paper)
        att = self._pdf_for(p, attachment)
        texts = self._pages(att)
        if not texts:
            raise ToolError(f"No text could be extracted from the PDF of '{self._label(p)}'.")
        want = parse_pages(pages, len(texts))
        beyond = [int(x) for x in re.findall(r"\d+", pages) if int(x) > len(texts)]
        given: list[int] = []
        chars = 0
        parts = [
            f"{self._label(p)} — {p.get('title') or ''} (PDF, {len(texts)} pages). "
            f"Cite as [@{p.get('citation_key') or '?'}, p. N] with N the page number below."
        ]
        for n in want:
            t = texts[n - 1].strip()
            if len(given) >= MAX_PAGES_PER_READ or (given and chars + len(t) > MAX_CHARS_PER_READ):
                break
            parts.append(f"=== Page {n} ===\n{t or '[no text on this page]'}")
            given.append(n)
            chars += len(t)
        if beyond:
            parts.append(f"[The PDF has {len(texts)} pages; the pages after {len(texts)} you asked for do not exist.]")
        rest = [n for n in want if n not in given]
        if rest:
            parts.append(
                f"[Not included (at most {MAX_PAGES_PER_READ} pages / {MAX_CHARS_PER_READ} characters "
                f"per call): pages {page_ranges(rest)}. Ask for them in another call if needed.]"
            )
        return "\n\n".join(parts)

    def view_page(self, paper: str, page: int, attachment: int | None = None):
        if not pdfrender.available():
            raise ToolError(pdfrender.MISSING)
        p = self._paper(paper)
        att = self._pdf_for(p, attachment)
        path = self.s.papers.attachment_path(att)
        n = self._page_count(att) or pdfrender.page_count(path)
        if not 1 <= page <= n:
            raise ToolError(f"Page {page} does not exist: the PDF has {n} pages.")
        try:
            figures = pdfregions.figure_regions(path, page)
        except Exception:
            figures = []
        try:
            equations = pdfregions.equation_regions(path, page)
        except Exception:
            equations = []
        boxed = [f for f in figures if f.get("rect")] + equations
        png = pdfrender.page_view(path, page, boxed)
        info = {
            "paper": self._label(p),
            "page": page,
            "pages_total": n,
            "grid": "Blue lines every 0.1 of the page width/height, labelled in the margins (0 = top/left). "
            "Regions are [x, y, w, h] in these fractions.",
            "figures": [
                drop_empty(
                    {
                        "box": boxed.index(f) + 1 if f in boxed else None,
                        "label": f["label"],
                        "caption": f["caption"],
                        "rect": f.get("rect"),
                        "no_region": None
                        if f.get("rect")
                        else "caption found, but no drawing above/below it was recognised (a vector or text "
                        "figure, or a rotated page): read the region off the grid and pass rect",
                    }
                )
                for f in figures
            ],
            "equations": [{"box": boxed.index(e) + 1, "label": e["label"], "rect": e["rect"]} for e in equations],
            "next": 'snapshot(paper, figure="Figure N") or snapshot(paper, equation="N") takes a proposed '
            "(red, numbered) region; or pass page and rect=[x, y, w, h] read off the grid.",
        }
        if not figures:
            info["figures_note"] = "No figure or table captions were recognised on this page."
        if not equations:
            del info["equations"]
        return Parts([Image(png), info])

    def _find_region(self, att: dict, path, kind: str, number: str, page: int | None) -> tuple[int, dict]:
        """(page, region) of a figure, table or numbered equation; on the given page, or else the
        first page that has it (pages whose text mentions it are tried first)."""
        fold = lambda x: x.casefold().replace("–", "-")
        if kind == "equation":
            want = fold(number)
            find = lambda pg: next(
                (r for r in pdfregions.equation_regions(path, pg) if fold(r["number"]) == want), None
            )
            name = f"Equation ({number})"
            mention = re.compile(r"\(\s*" + re.escape(number) + r"\s*\)", re.I)
        else:
            want = f"{'Table' if kind == 'table' else 'Figure'} {number}"
            find = lambda pg: next(
                (r for r in pdfregions.figure_regions(path, pg) if fold(r["label"]) == fold(want)), None
            )
            name = want
            mention = re.compile(r"(?:fig(?:ure)?|tab(?:le)?)\.?\s*" + re.escape(number) + r"\b", re.I)
        n = self._page_count(att) or pdfrender.page_count(path)
        if page is not None:
            if not 1 <= page <= n:
                raise ToolError(f"Page {page} does not exist: the PDF has {n} pages.")
            hit = find(page)
            if hit:
                return page, hit
            labels = [r["label"] for r in pdfregions.page_regions(path, page)]
            raise ToolError(
                f"No {name} on page {page}"
                + (f" (found: {', '.join(labels)})" if labels else "")
                + ". Leave out page to search the whole PDF, or use view_page and pass rect=[x, y, w, h]."
            )
        try:
            texts = self._pages(att)
        except Exception:
            texts = []
        likely = [i for i, t in enumerate(texts, 1) if mention.search(t)]
        for pg in likely + [i for i in range(1, min(n, 300) + 1) if i not in likely]:
            hit = find(pg)
            if hit:
                return pg, hit
        found = [] if kind == "equation" else [f"{c['label']} (p. {c['page']})" for c in pdfoutline.captions(texts)]
        raise ToolError(
            f"{name} was not found in the PDF. Equations are found by a number in parentheses at "
            f"the end of their line; figures and tables by their caption"
            + (f" (captions found: {', '.join(found[:20])}{'…' if len(found) > 20 else ''})" if found else "")
            + ". Use view_page on the page and pass rect=[x, y, w, h]."
        )

    def snapshot(
        self,
        paper: str,
        page: int | None = None,
        figure: str | None = None,
        equation: str | None = None,
        rect: list | None = None,
        attachment: int | None = None,
    ):
        if not pdfrender.available():
            raise ToolError(pdfrender.MISSING)
        if sum(x is not None for x in (figure, equation, rect)) != 1:
            raise ToolError(
                'Give either figure (e.g. "Figure 2", "Table 1"), equation (e.g. "3" for (3)) '
                "or page and rect=[x, y, w, h]."
            )
        p = self._paper(paper)
        key = p.get("citation_key")
        if not key:
            raise ToolError(f"'{self._label(p)}' has no citation key, so a snapshot could not be cited.")
        att = self._pdf_for(p, attachment)
        path = self.s.papers.attachment_path(att)
        label = None
        if rect is None:
            if equation is not None:
                parsed = pdfregions.parse_label(
                    equation if re.match(r"\s*(eq|\()", str(equation), re.I) else f"({str(equation).strip()})"
                )
            else:
                parsed = pdfregions.parse_label(figure or "")
            if not parsed:
                raise ToolError(
                    f"Could not read '{equation if equation is not None else figure}': write e.g. "
                    f'figure="Figure 2", figure="Table 1" or equation="3".'
                )
            page, hit = self._find_region(att, path, parsed[0], parsed[1], page)
            if not hit.get("rect"):
                raise ToolError(
                    f"{hit['label']} was found on page {page} but not its region. "
                    f"Use view_page and pass rect=[x, y, w, h]."
                )
            rect, label = hit["rect"], hit["label"]
        else:
            if page is None:
                raise ToolError("rect needs the page it is on.")
            n = self._page_count(att) or pdfrender.page_count(path)
            if not 1 <= page <= n:
                raise ToolError(f"Page {page} does not exist: the PDF has {n} pages.")
        if len(rect) != 4 or not all(isinstance(v, (int, float)) for v in rect):
            raise ToolError("rect must be four numbers [x, y, w, h], fractions of the page from the top left.")
        x, y, w, h = (float(v) for v in rect)
        x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
        w, h = min(w, 1.0 - x), min(h, 1.0 - y)
        if w < 0.01 or h < 0.01:
            raise ToolError("The region is too small (or outside the page).")
        png, pw, ph = pdfrender.render_region(path, page, [x, y, w, h])
        saved = self.s.note_service.add_image(f"{key}-p{page}.png", png)
        preview, _, _ = pdfrender.render_region(path, page, [x, y, w, h], scale=2.0, max_side=900)
        caption = label or "Figure"
        markdown = f"![{key} p. {page}]({saved['path']})\n*{caption}: [@{key}, p. {page}]*"
        return Parts(
            [
                Image(preview),
                drop_empty(
                    {
                        "saved": saved["path"],
                        "label": label,
                        "size": f"{pw}x{ph}",
                        "region": {"page": page, "rect": [round(v, 4) for v in (x, y, w, h)]},
                        "markdown": markdown,
                        "how_to_use": "Paste the markdown into the note text (write_note or edit_note), as is. You may write a "
                        f"caption after '{caption}:' but keep the citation on the line right below the image. If "
                        "the crop is wrong, call snapshot again with page and a corrected rect (unused images are "
                        "cleaned up later).",
                    }
                ),
            ]
        )
