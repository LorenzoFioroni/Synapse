"""BibTeX / BibLaTeX import and export."""

from __future__ import annotations

import re

from ..library import citekeys
from .common import join_authors, parse_display_name, split_authors, split_keywords, year_from
from .latex import escape, latex_to_unicode, protect_case

MONTHS = {
    m: str(i)
    for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)
}

TYPE_IN = {
    "article": "journalArticle",
    "book": "book",
    "inbook": "bookSection",
    "incollection": "bookSection",
    "inproceedings": "conferencePaper",
    "conference": "conferencePaper",
    "proceedings": "book",
    "phdthesis": "thesis",
    "mastersthesis": "thesis",
    "thesis": "thesis",
    "techreport": "report",
    "report": "report",
    "unpublished": "manuscript",
    "online": "webpage",
    "electronic": "webpage",
    "www": "webpage",
    "dataset": "dataset",
    "misc": "journalArticle",
    "booklet": "book",
    "manual": "book",
}
TYPE_OUT = {
    "journalArticle": "article",
    "book": "book",
    "bookSection": "incollection",
    "conferencePaper": "inproceedings",
    "thesis": "phdthesis",
    "report": "techreport",
    "preprint": "misc",
    "manuscript": "unpublished",
    "webpage": "misc",
    "dataset": "misc",
}


# ---------------------------------------------------------------- tokenizer
class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.n = len(text)
        self.strings = dict(MONTHS)

    def ws(self):
        while self.i < self.n and self.s[self.i].isspace():
            self.i += 1

    def ident(self) -> str:
        self.ws()
        start = self.i
        while self.i < self.n and (self.s[self.i].isalnum() or self.s[self.i] in "_-:.+/'!?*&"):
            self.i += 1
        return self.s[start : self.i]

    def braced(self) -> str:
        # assumes s[i] == "{"
        depth, start = 0, self.i + 1
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    self.i += 1
                    return self.s[start : self.i - 1]
            self.i += 1
        raise ValueError("Unbalanced braces")

    def quoted(self) -> str:
        self.i += 1
        depth, start = 0, self.i
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == '"' and depth == 0:
                self.i += 1
                return self.s[start : self.i - 1]
            self.i += 1
        raise ValueError("Unterminated string")

    def value(self) -> str:
        parts = []
        while True:
            self.ws()
            c = self.s[self.i] if self.i < self.n else ""
            if c == "{":
                parts.append(self.braced())
            elif c == '"':
                parts.append(self.quoted())
            else:
                tok = self.ident()
                if not tok:
                    raise ValueError(f"Unexpected character {c!r} at {self.i}")
                parts.append(self.strings.get(tok.lower(), tok))
            self.ws()
            if self.i < self.n and self.s[self.i] == "#":
                self.i += 1
                continue
            return "".join(parts)

    def skip_balanced(self):
        self.ws()
        if self.i < self.n and self.s[self.i] in "{(":
            open_, close = self.s[self.i], "}" if self.s[self.i] == "{" else ")"
            depth = 0
            while self.i < self.n:
                if self.s[self.i] == open_:
                    depth += 1
                elif self.s[self.i] == close:
                    depth -= 1
                    if depth == 0:
                        self.i += 1
                        return
                self.i += 1

    def entries(self):
        out = []
        while True:
            at = self.s.find("@", self.i)
            if at < 0:
                return out
            self.i = at + 1
            etype = self.ident().lower()
            self.ws()
            if self.i >= self.n or self.s[self.i] not in "{(":
                continue
            if etype in ("comment", "preamble"):
                self.skip_balanced()
                continue
            close = "}" if self.s[self.i] == "{" else ")"
            self.i += 1
            try:
                if etype == "string":
                    name = self.ident()
                    self.ws()
                    self.i += 1  # '='
                    self.strings[name.lower()] = self.value()
                    self.ws()
                    if self.i < self.n and self.s[self.i] == close:
                        self.i += 1
                    continue
                self.ws()
                key_start = self.i
                while self.i < self.n and self.s[self.i] not in ",\n" + close:
                    self.i += 1
                key = self.s[key_start : self.i].strip()
                fields = {}
                while True:
                    self.ws()
                    if self.i >= self.n:
                        break
                    c = self.s[self.i]
                    if c == ",":
                        self.i += 1
                        continue
                    if c == close:
                        self.i += 1
                        break
                    name = self.ident().lower()
                    self.ws()
                    if not name or self.i >= self.n or self.s[self.i] != "=":
                        # garbage: skip to next comma/close
                        while self.i < self.n and self.s[self.i] not in "," + close:
                            self.i += 1
                        continue
                    self.i += 1
                    fields[name] = self.value()
                out.append((etype, key, fields))
            except (ValueError, IndexError):
                # skip the broken entry and resume scanning right after its "@"
                self.i = at + 1
                continue


# ------------------------------------------------------------------- import
def _authors(raw: str) -> str:
    names = [n for n in re.split(r"\s+and\s+(?![^{}]*\})", raw.strip()) if n.strip()]
    pairs = []
    for n in names:
        n = n.strip()
        if n.startswith("{") and n.endswith("}") and n.count("{") == 1:
            pairs.append((latex_to_unicode(n), ""))  # corporate author
            continue
        if n.lower() == "others":
            continue
        n_u = latex_to_unicode(n)
        if "," in n_u:
            bits = [b.strip() for b in n_u.split(",")]
            fam, giv = bits[0], bits[-1] if len(bits) > 1 else ""
            if len(bits) == 3:  # von Last, Jr, First
                fam = f"{bits[0]}, {bits[1]}".replace(",", "")
            pairs.append((fam, giv))
        else:
            pairs.append(parse_display_name(n_u))
    return join_authors(pairs)


def parse(text: str) -> list[dict]:
    items = []
    for etype, key, f in _Parser(text).entries():
        g = {k: latex_to_unicode(v) for k, v in f.items() if k not in ("author", "editor", "file")}
        itype = TYPE_IN.get(etype, "journalArticle")
        if etype == "misc" and (
            g.get("eprint") or "arxiv" in (g.get("archiveprefix", "") + g.get("eprinttype", "")).lower()
        ):
            itype = "preprint"
        if etype == "misc" and g.get("howpublished", "").startswith("http") and not g.get("journal"):
            itype = "webpage"
        item = {
            "item_type": itype,
            "citation_key": key,
            "title": g.get("title", ""),
            "authors": _authors(f["author"]) if f.get("author") else (_authors(f["editor"]) if f.get("editor") else ""),
            "year": year_from(g.get("year") or g.get("date")),
            "journal": g.get("journal") or g.get("journaltitle") or g.get("booktitle") or "",
            "volume": g.get("volume", ""),
            "issue": g.get("number") or g.get("issue", ""),
            "pages": g.get("pages", "").replace("–", "-"),
            "publisher": g.get("publisher") or g.get("school") or g.get("institution") or g.get("organization", ""),
            "place": g.get("address") or g.get("location", ""),
            "edition": g.get("edition", ""),
            "series": g.get("series", ""),
            "isbn": g.get("isbn", ""),
            "issn": g.get("issn", ""),
            "doi": re.sub(r"^https?://(dx\.)?doi\.org/", "", g.get("doi", ""), flags=re.I),
            "url": g.get("url") or (g.get("howpublished") if g.get("howpublished", "").startswith("http") else ""),
            "abstract": g.get("abstract", ""),
            "keywords": "; ".join(split_keywords(g.get("keywords") or g.get("keyword", ""))),
            "language": g.get("language") or g.get("langid", ""),
            "short_title": g.get("shorttitle", ""),
            "extra": g.get("note") or g.get("annote", ""),
            "pmid": g.get("pmid", ""),
            "pmcid": g.get("pmcid", ""),
        }
        if g.get("eprint"):
            prefix = g.get("archiveprefix") or g.get("eprinttype") or "arXiv"
            item["archive"] = prefix
            item["archive_id"] = f"{prefix}:{g['eprint']}" if prefix.lower() == "arxiv" else g["eprint"]
            if itype == "preprint" and not item["journal"]:
                item["journal"] = prefix
        items.append({k: v for k, v in item.items() if v not in ("", None)})
    return items


# ------------------------------------------------------------------- export
def _field(name: str, value, unicode: bool, raw: bool = False) -> str:
    v = str(value) if raw else escape(str(value), unicode)
    return f"  {name} = {{{v}}},"


def dump(items: list[dict], *, unicode: bool = True, abstract: bool = True, pattern: str = "{auth}{year}") -> str:
    taken: set[str] = set()
    chunks = []
    for it in items:
        itype = it.get("item_type") or "journalArticle"
        btype = TYPE_OUT.get(itype, "misc")
        key = it.get("citation_key") or citekeys.render(pattern, it)
        key = citekeys.make_unique(key, taken)
        taken.add(key)
        lines = [f"@{btype}{{{key},"]
        if it.get("title"):
            lines.append(f"  title = {{{protect_case(escape(it['title'], unicode))}}},")
        pairs = split_authors(it.get("authors"))
        if pairs:
            names = " and ".join(
                ("{" + escape(f, unicode) + "}")
                if not g and " " in f
                else (f"{escape(f, unicode)}, {escape(g, unicode)}" if g else escape(f, unicode))
                for f, g in pairs
            )
            lines.append(f"  author = {{{names}}},")
        container = it.get("journal")
        if container:
            name = {"article": "journal", "incollection": "booktitle", "inproceedings": "booktitle"}.get(btype)
            if name:
                lines.append(_field(name, container, unicode))
            elif itype == "preprint":
                pass
            else:
                lines.append(_field("howpublished" if btype == "misc" else "journal", container, unicode))
        if it.get("year"):
            lines.append(_field("year", it["year"], unicode))
        for src, dst in (("volume", "volume"), ("issue", "number")):
            if it.get(src):
                lines.append(_field(dst, it[src], unicode))
        if it.get("pages"):
            lines.append(
                _field(
                    "pages",
                    str(it["pages"]).replace("–", "-").replace("-", "--").replace("----", "--"),
                    unicode,
                    raw=True,
                )
            )
        if it.get("publisher"):
            pub_name = {"thesis": "school", "report": "institution"}.get(itype, "publisher")
            lines.append(_field(pub_name, it["publisher"], unicode))
        for src, dst in (
            ("place", "address"),
            ("edition", "edition"),
            ("series", "series"),
            ("isbn", "isbn"),
            ("issn", "issn"),
            ("language", "langid"),
            ("short_title", "shorttitle"),
            ("pmid", "pmid"),
            ("pmcid", "pmcid"),
        ):
            if it.get(src):
                lines.append(_field(dst, it[src], unicode))
        if it.get("doi"):
            lines.append(_field("doi", it["doi"], unicode, raw=True))
        if it.get("url"):
            lines.append(_field("url", it["url"], unicode, raw=True))
        aid = it.get("archive_id") or ""
        if aid.lower().startswith("arxiv:"):
            lines.append(_field("eprint", aid.split(":", 1)[1], unicode, raw=True))
            lines.append("  archiveprefix = {arXiv},")
        if it.get("keywords"):
            lines.append(_field("keywords", ", ".join(split_keywords(it["keywords"])), unicode))
        if abstract and it.get("abstract"):
            lines.append(_field("abstract", it["abstract"], unicode))
        if it.get("extra"):
            lines.append(_field("note", it["extra"], unicode))
        lines[-1] = lines[-1].rstrip(",") if len(lines) > 1 else lines[-1]
        lines.append("}")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks) + ("\n" if chunks else "")


def sniff(text: str) -> bool:
    return bool(re.search(r"@\s*[A-Za-z]+\s*[{(]", text))
