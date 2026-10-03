"""Local normalisation rules and issue detection.

Every rule only *proposes* changes.  Proposals are grouped per item/field and
composed: all enabled rules for one field produce a single proposal listing
the rules involved, so the user reviews one value per field.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from . import latex
from . import textutil as tu

NON_REGULAR = ("note", "attachment", "annotation")
EXPECT_CREATORS = {"journalArticle", "conferencePaper", "book", "bookSection", "thesis", "preprint",
                   "report", "manuscript", "magazineArticle", "newspaperArticle", "patent"}
EXPECT_DATE = EXPECT_CREATORS | {"webpage", "dataset", "presentation"}
EXPECT_PUB = {"journalArticle": "publicationTitle", "conferencePaper": "proceedingsTitle",
              "bookSection": "bookTitle"}
ABBREV_END = re.compile(r"(?:\bet al|\betc|\bInc|\bLtd|\bCo|\bJr|\bSr|\bvs|\bNo|\bVol|\bSt|\b[A-Z])\.$")
MONTHS = ("jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|"
          "august|september|october|november|december|janvier|février|fevrier|mars|avril|mai|juin|juillet|"
          "août|aout|septembre|octobre|novembre|décembre|decembre|januar|februar|märz|maerz|juni|juli|"
          "oktober|dezember|gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|dicembre")
WEEKDAYS = "mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
STRING_SKIP = {"key", "version", "itemType", "creators", "tags", "collections", "relations", "dateAdded",
               "dateModified", "accessDate", "deleted", "parentItem", "inPublications", "note",
               "md5", "mtime", "filename", "path", "contentType", "charset", "linkMode"}


class Context:
    """Library-wide information needed by some rules."""

    def __init__(self, settings, schema, items: list[dict], type_history: dict | None = None, arxiv_info=None,
                 bases: dict | None = None, user_fixed: set | None = None):
        self.settings = settings
        self.schema = schema
        self.type_history = type_history or {}
        # (item key, field) -> value before this tool's own automatic changes (see service.compute_bases)
        self.bases = bases or {}
        self.user_fixed = user_fixed or set()  # (item key, field) whose current value you typed yourself
        self.arxiv_info = arxiv_info or (lambda ax: None)  # cached arXiv record (published DOI, journal ref)
        self.regular = [i for i in items if i["itemType"] not in NON_REGULAR and not i["deleted"]]
        lat = settings.get("latex")
        self.latex_on = bool(lat.get("enabled")) and settings.rule("latex")
        self.latex_escape = bool(lat.get("escape_specials")) and settings.rule("latex_escape") and bool(lat.get("enabled"))
        self.latex_style = lat.get("style", "braced")
        self.latex_bases = set(lat.get("fields") or [])
        if lat.get("include_abstract"):
            self.latex_bases.add("abstractNote")
        self.acronyms = set(settings.get("acronyms") or [])
        self.proxies = settings.get("proxy_suffixes") or []
        self._build_vocab()
        self._build_journals()
        self._build_tags()
        self._build_ids()

    # ------------------------------------------------------------------
    def type_change_allowed(self, key: str, target: str, publishing: bool = False) -> bool:
        """Only your explicit rejections block a type change; everything else must
        converge because all rules agree on the target type."""
        h = self.type_history.get(key)
        return not (h and target in h["rejected"])

    def is_latex_field(self, item_type: str, field: str) -> bool:
        return field in self.latex_bases or self.schema.base_of(item_type, field) in self.latex_bases

    def _build_vocab(self):
        """Proper nouns = words that are capitalised mid-title in sentence-case titles."""
        cap, low = Counter(), Counter()
        for it in self.regular:
            t = it["data"].get("title") or ""
            if not t or tu.is_shouting(t):
                continue
            words = latex.to_unicode(t).split()
            mid = [w.strip("()[]\"'.,:;!?") for w in words[1:]]
            mid = [w for w in mid if w.isalpha()]
            if not mid:
                continue
            capped = sum(1 for w in mid if w[0].isupper() and w.lower() not in tu.SMALL_WORDS)
            if capped / len(mid) > 0.5:
                continue  # title-case title: tells us nothing
            for w in mid:
                if w[0].isupper() and w[1:].islower():
                    cap[w.lower()] += 1
                elif w.islower():
                    low[w] += 1
        self.proper_nouns = {w for w, n in cap.items() if n >= 2 and n / (n + low[w]) >= 0.6}

    def _journal_key(self, name: str) -> str:
        k = tu.canon(name.replace("&", " and ").replace("\\&", " and "))
        k = re.sub(r"^the ", "", k)
        return k

    def _build_journals(self):
        forms = defaultdict(Counter)
        abbr = defaultdict(Counter)
        for it in self.regular:
            d = it["data"]
            f = self.schema.actual(it["itemType"], "publicationTitle") or "publicationTitle"
            name = (d.get(f) or "").strip()
            if not name or it["itemType"] != "journalArticle":
                continue
            k = self._journal_key(name)
            forms[k][tu.collapse_ws(name)] += 1
            a = (d.get("journalAbbreviation") or "").strip()
            if a:
                abbr[k][a] += 1
        self.journal_pref = {}
        for k, c in forms.items():
            if len(c) > 1:
                # most common; ties broken by preferring forms that are not ALL CAPS and have more capitals/accents
                best = sorted(c.items(), key=lambda kv: (-kv[1], tu.is_shouting(kv[0]), -len(latex.to_unicode(kv[0]))))[0][0]
                self.journal_pref[k] = best
        self.journal_abbr = {k: c.most_common(1)[0][0] for k, c in abbr.items()}

    def _build_tags(self):
        forms = defaultdict(Counter)
        for it in self.regular:
            for t in it["data"].get("tags", []) or []:
                name = t.get("tag", "")
                forms[tu.canon(name) or name][name] += 1
        self.tag_pref = {}
        for k, c in forms.items():
            if len(c) > 1:
                best = sorted(c.items(), key=lambda kv: (-kv[1], kv[0] != kv[0].strip(), kv[0]))[0][0]
                for name in c:
                    if name != best:
                        self.tag_pref[name] = best

    def _build_ids(self):
        self.doi_map = defaultdict(list)
        for it in self.regular:
            d = tu.clean_doi(it["data"].get("DOI", "")).lower()
            if d:
                self.doi_map[d].append(it["key"])


# ----------------------------------------------------------------------
# Field-level pipeline
# ----------------------------------------------------------------------

def normalize_value(ctx: Context, item_type: str, field: str, value: str, *, external: bool = False):
    """Return (new_value, rules, confidence, notes) for one string field."""
    s = ctx.settings
    rules: list[str] = []
    conf = 1.0
    notes: list[str] = []
    v = value if value is not None else ""
    if not isinstance(v, str):
        return value, [], 1.0, []
    base = ctx.schema.base_of(item_type, field)
    is_latex = ctx.is_latex_field(item_type, field)

    def step(rule_id, new, c):
        nonlocal v, conf
        if new != v:
            if not external and not s.rule(rule_id):
                return
            rules.append(rule_id)
            conf = min(conf, c)
            v = new

    # 1. whitespace
    step("whitespace", tu.collapse_ws(v, keep_newlines=field in ("abstractNote", "extra")), 0.97)
    # 2. markup
    if field not in ("url", "DOI", "extra") and tu.has_markup(v):
        if is_latex and ctx.latex_on:
            new, cx = tu.html_to_latex(v, style=ctx.latex_style, escape_specials=ctx.latex_escape)
        else:
            new = tu.plain(v) if field != "abstractNote" else tu.collapse_ws(tu.plain(v), keep_newlines=True)
            cx = False
        step("markup", new, 0.6 if cx else 0.85)
        if cx:
            notes.append("MathML/complex markup was flattened — check the formula")
    # 3. LaTeX encoding
    if is_latex and ctx.latex_on:
        a = latex.to_latex(v, style=ctx.latex_style, escape_specials=False)
        step("latex", a, 0.92)
        if ctx.latex_escape:
            b = latex.to_latex(v, style=ctx.latex_style, escape_specials=True)
            step("latex_escape", b, 0.9)
    # 4. field-specific
    if field == "DOI":
        step("doi", tu.clean_doi(v), 0.97)
    elif field == "pages":
        step("pages", tu.normalize_pages(v, s.get("pages_separator") or "--"), 0.9)
    elif field == "ISSN":
        step("issn_isbn", tu.normalize_issn_field(v), 0.9)
    elif field == "ISBN":
        step("issn_isbn", tu.normalize_isbn_field(v), 0.9)
    elif field == "url":
        step("url", tu.clean_url(v, ctx.proxies), 0.85)
    elif field == "language":
        step("language", tu.normalize_language(v), 0.85)
    elif field == "extra":
        lines, seen = [], set()
        for ln in v.splitlines():
            if ln.strip() and ln.strip() not in seen:
                seen.add(ln.strip())
                lines.append(ln.rstrip())
        step("extra", "\n".join(lines), 0.9)
    elif base in ("title", "shortTitle"):
        if tu.is_shouting(v):
            step("title_caps", tu.to_sentence_case(v, acronyms=ctx.acronyms, proper_nouns=ctx.proper_nouns,
                                                   from_all_caps=True), 0.5)
            if "title_caps" in rules:
                notes.append("was ALL CAPS — check proper nouns and acronyms")
        else:
            pol = s.get("title_case")
            if pol == "sentence":
                step("title_case", tu.to_sentence_case(v, acronyms=ctx.acronyms, proper_nouns=ctx.proper_nouns), 0.45)
            elif pol == "title":
                step("title_case", tu.to_title_case(v), 0.5)
        if v.endswith(".") and not v.endswith("..") and not ABBREV_END.search(tu.plain(v)) and not v.endswith("$."):
            step("title_period", v[:-1].rstrip(), 0.8)
    elif base == "publicationTitle" and tu.is_shouting(v):
        step("title_caps", tu.to_title_case(tu.to_sentence_case(v, acronyms=ctx.acronyms, from_all_caps=True)), 0.5)
    return v, rules, conf, notes


def normalize_creators(ctx: Context, item_type: str, creators: list[dict], *, external=False):
    s = ctx.settings
    rules: list[str] = []
    conf = 1.0
    notes = []
    latex_names = ctx.latex_on and "creators" in ctx.latex_bases
    two_field_share = sum(1 for c in creators if "lastName" in c) / max(1, len(creators))
    out = []
    seen = set()

    def use(rule, c):
        nonlocal conf
        if external or s.rule(rule):
            if rule not in rules:
                rules.append(rule)
            conf = min(conf, c)
            return True
        return False

    for c in creators:
        c = dict(c)
        # whitespace + LaTeX in all name parts
        for k in ("firstName", "lastName", "name"):
            if k in c and isinstance(c[k], str):
                v0 = c[k]
                v = tu.collapse_ws(v0)
                if v != v0 and use("whitespace", 0.97):
                    c[k] = v
                if tu.has_markup(c[k]) and use("markup", 0.85):
                    c[k] = tu.plain(c[k])
                if latex_names:
                    v2 = latex.to_latex(c[k], style=ctx.latex_style, escape_specials=ctx.latex_escape)
                    if v2 != c[k] and use("latex", 0.92):
                        c[k] = v2
        # drop empty creators
        if not any((c.get(k) or "").strip() for k in ("firstName", "lastName", "name")):
            if use("creators", 0.9):
                notes.append("removed an empty creator")
                continue
        # single-field -> two-field
        if "name" in c and c.get("name"):
            sp = tu.split_single_field_name(c["name"])
            if sp:
                last, first, cf = sp
                if cf < 0.8 and two_field_share < 0.5:
                    sp = None  # 'First Last' only when the other creators are two-field
                if sp and use("creators", cf):
                    c = {"creatorType": c.get("creatorType", "author"), "firstName": first, "lastName": last}
        # 'Last, First' stuffed into lastName
        if "lastName" in c and not (c.get("firstName") or "").strip() and (c.get("lastName") or "").count(",") == 1:
            last, first = [x.strip() for x in c["lastName"].split(",")]
            if last and first and use("creators", 0.85):
                c["lastName"], c["firstName"] = last, first
        # swapped: empty lastName, full name in firstName
        if "lastName" in c and not (c.get("lastName") or "").strip() and (c.get("firstName") or "").strip():
            sp = tu.split_single_field_name(c["firstName"])
            if sp and use("creators", 0.6):
                c["lastName"], c["firstName"] = sp[0], sp[1]
        # ALL CAPS names
        for k in ("lastName", "firstName"):
            v = c.get(k) or ""
            u = latex.to_unicode(v)
            letters = [ch for ch in u if ch.isalpha()]
            if len(letters) >= 3 and all(ch.isupper() for ch in letters) and not (k == "firstName" and re.fullmatch(r"[A-Z .\-]+", u) and len(letters) <= 3):
                nv = tu.name_proper_case(v)
                if nv != v and use("creators", 0.7):
                    c[k] = nv
        # initials
        if c.get("firstName"):
            nv = tu.normalize_initials(c["firstName"])
            if nv != c["firstName"] and use("initials", 0.85):
                c["firstName"] = nv
        # duplicates
        sig = (c.get("creatorType"), tu.canon(c.get("lastName") or c.get("name") or ""), tu.canon(c.get("firstName") or ""))
        if sig in seen and sig[1]:
            if use("creators", 0.6):
                notes.append("removed a duplicated creator")
                continue
        seen.add(sig)
        out.append(c)
    return out, rules, conf, notes


# ----------------------------------------------------------------------
# Item-level
# ----------------------------------------------------------------------

def _date_proposal(raw: str, parsed: str):
    if not raw or not parsed or raw.strip() == parsed:
        return None
    r = raw.strip().lower()
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", r):
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(z|[+-]\d{2}:?\d{2})?", r):
        return parsed if len(parsed) == 10 else None, 0.9
    words = re.findall(r"[^\W\d_]+", r)
    ok_words = re.compile(rf"^(?:{MONTHS}|{WEEKDAYS}|st|nd|rd|th|of|de|der)$")
    if any(not ok_words.match(w) for w in words):
        return None  # seasons, 'in press', ranges ... keep as typed
    nums = re.findall(r"\d+", r)
    has_month_name = any(re.match(rf"^(?:{MONTHS})$", w) for w in words)
    comps = len(nums) + (1 if has_month_name else 0)
    want = {1: 4, 2: 7, 3: 10}.get(comps)
    if want is None or len(parsed) != want:
        return None
    conf = 0.8
    if not has_month_name and len(nums) == 3:
        small = [int(n) for n in nums if len(n) <= 2]
        if len(small) >= 2 and all(1 <= n <= 12 for n in small[:2]) and small[0] != small[1]:
            conf = 0.35  # 03/04/2019: day/month ambiguous
    return parsed, conf


def item_proposals(ctx: Context, it: dict) -> tuple[list[dict], list[dict]]:
    """Return (proposals, issues) for one regular item."""
    d, key, typ = it["data"], it["key"], it["itemType"]
    props: list[dict] = []
    issues: list[dict] = []
    s = ctx.settings

    def prop(field, old, new, rules, conf, note="", kind="field", group=None, base=None):
        p = {"item_key": key, "kind": kind, "field": field, "old": old, "new": new, "rules": rules,
             "confidence": round(conf, 2), "note": note, "source": "rules", "group_id": group}
        if base is not None:
            p["base"] = base  # new == f(base): lets later runs recompute from the same starting point
        props.append(p)

    def issue(code, sev, msg, **details):
        issues.append({"item_key": key, "code": code, "severity": sev, "message": msg, "details": details, "source": "rules"})

    pending: dict[str, tuple] = {}  # field -> (new, rules, conf, notes)
    bases_used: dict[str, object] = {}  # field -> value the pipeline was computed from

    # ---- string fields
    for f, v in d.items():
        if f in STRING_SKIP or not isinstance(v, str) or not v or (key, f) in ctx.user_fixed:
            continue
        base = ctx.bases.get((key, f))
        new, rules, conf, notes = normalize_value(ctx, typ, f, base if base is not None else v)
        if new != v and (rules or base is not None):
            if base is not None and base != v:
                rules = rules or ["recompute"]
                notes = notes + ["recomputed from the value before earlier automatic changes"]
            pending[f] = (new, rules, conf, notes)
            bases_used[f] = base if base is not None else v

    def cur(f):
        return pending[f][0] if f in pending else (d.get(f) or "")

    def merge(f, new, rule, conf, note=None):
        bases_used.pop(f, None)  # item-level change: not a pure function of the field
        if f in pending:
            n, r, c, nn = pending[f]
            pending[f] = (new, r + [rule], min(c, conf), nn + ([note] if note else []))
        else:
            pending[f] = (new, [rule], conf, [note] if note else [])

    # ---- DOI from URL / Extra
    if s.rule("doi") and ctx.schema.has_field(typ, "DOI") and not cur("DOI"):
        u = d.get("url", "")
        found, where = "", ""
        m = re.search(r"doi\.org/(10\.\d{4,9}/\S+)", u or "", re.I)
        if m:
            found, where = tu.clean_doi(m.group(1)), "URL"
        extra = d.get("extra") or ""
        m2 = re.search(r"^\s*DOI:\s*(\S+)\s*$", extra, re.I | re.M)
        if not found and m2:
            found, where = tu.clean_doi(m2.group(1)), "Extra"
        if found and tu.DOI_STRICT.match(found):
            merge("DOI", found, "doi", 0.9, f"found in {where}")
            if where == "Extra":
                new_extra = re.sub(r"^\s*DOI:\s*\S+\s*\n?", "", cur("extra"), flags=re.I | re.M).strip()
                merge("extra", new_extra, "doi", 0.9, "DOI moved to the DOI field")

    # ---- journal names & abbreviations
    pub_f = ctx.schema.actual(typ, "publicationTitle")
    if typ == "journalArticle" and pub_f and cur(pub_f):
        k = ctx._journal_key(cur(pub_f))
        pref = ctx.journal_pref.get(k)
        if pref and s.rule("journal_names"):
            pref_n = normalize_value(ctx, typ, pub_f, pref)[0]
            if pref_n != cur(pub_f):
                merge(pub_f, pref_n, "journal_names", 0.7, f"most common spelling in your library")
        ab = ctx.journal_abbr.get(k)
        if ab and s.rule("journal_abbrev") and ctx.schema.has_field(typ, "journalAbbreviation") and not cur("journalAbbreviation"):
            merge("journalAbbreviation", normalize_value(ctx, typ, "journalAbbreviation", ab)[0], "journal_abbrev", 0.7,
                  "used by other items of this journal")

    # ---- date
    if s.rule("date") and d.get("date"):
        r = _date_proposal(d["date"], (it.get("meta") or {}).get("parsedDate", ""))
        if r and r[0]:
            merge("date", r[0], "date", r[1], "day/month order is ambiguous" if r[1] < 0.5 else None)

    for f, (new, rules, conf, notes) in pending.items():
        if new != (d.get(f) or ""):
            prop(f, d.get(f, ""), new, rules, conf, "; ".join(n for n in notes if n), base=bases_used.get(f))

    # ---- creators
    cr = d.get("creators") or []
    if cr and (key, "creators") not in ctx.user_fixed:
        base = ctx.bases.get((key, "creators"))
        new_cr, rules, conf, notes = normalize_creators(ctx, typ, base if base is not None else cr)
        if base is not None and new_cr != cr:
            rules = rules or ["recompute"]
            notes = notes + ["recomputed from the value before earlier automatic changes"]
        if rules and new_cr != cr:
            prop("creators", cr, new_cr, rules, conf, "; ".join(notes), kind="creators", base=base if base is not None else cr)

    # ---- tags
    if s.rule("tags") and d.get("tags"):
        tags = d["tags"]
        new_tags, changed = [], False
        names = set()
        for t in tags:
            name = t.get("tag", "")
            nn = ctx.tag_pref.get(name, name)
            nn2 = tu.collapse_ws(nn)
            if nn2 != name:
                changed = True
            if nn2 in names:
                changed = True
                continue
            names.add(nn2)
            new_tags.append(dict(t, tag=nn2))
        if changed:
            prop("tags", tags, new_tags, ["tags"], 0.75, "merged tag spelling variants", kind="tags")

    # ---- item type heuristics (arXiv stored as journal article)
    if s.rule("item_type") and typ == "journalArticle":
        pub = tu.plain(d.get("publicationTitle", "")).lower()
        ax = tu.arxiv_from(" ".join([d.get("url", ""), d.get("extra", ""), d.get("DOI", ""), d.get("publicationTitle", "")]))
        looks_preprint = ax and (not pub or pub.startswith("arxiv") or "preprint" in pub)
        doi = tu.clean_doi(d.get("DOI", "")).lower()
        if doi and not doi.startswith("10.48550/"):
            looks_preprint = False  # has the DOI of a published version: fill the journal instead
        info = ctx.arxiv_info(ax) if looks_preprint else None
        if info and (info.get("published_doi") or info.get("journal_ref")):
            looks_preprint = False  # arXiv knows it was published
        if re.search(r"^\s*(journal[ -]?ref(erence)?|published)\s*:", d.get("extra", "") or "", re.I | re.M):
            looks_preprint = False
        if looks_preprint and not ctx.type_change_allowed(key, "preprint"):
            looks_preprint = False
        if looks_preprint:
            new_data, dropped = ctx.schema.convert_type(d, "preprint")
            new_data["repository"] = "arXiv"
            new_data["archiveID"] = f"arXiv:{ax}"
            prop("itemType", typ, {"itemType": "preprint", "data": {k: v for k, v in new_data.items() if k not in ("key", "version")},
                                   "dropped": dropped, "set": {"repository": "arXiv", "archiveID": f"arXiv:{ax}"}}, ["item_type"], 0.6,
                 "arXiv paper stored as a journal article without a journal", kind="itemType")

    # ---- issues
    title = d.get("title", "")
    if not title.strip():
        issue("missing_title", "error", "Title is empty")
    if typ in EXPECT_CREATORS and not any((c.get("lastName") or c.get("name") or "").strip() for c in cr):
        issue("missing_creators", "error", "No authors/creators")
    if typ in EXPECT_DATE and not (d.get("date") or "").strip():
        issue("missing_date", "warning", "No date")
    if typ in EXPECT_PUB and not (d.get(EXPECT_PUB[typ]) or "").strip():
        issue("missing_publication", "warning", f"{EXPECT_PUB[typ]} is empty")
    doi = tu.clean_doi(cur("DOI"))
    if doi and not tu.DOI_STRICT.match(doi):
        issue("malformed_doi", "error", f"DOI looks malformed: {d.get('DOI')}")
    if doi and len(ctx.doi_map.get(doi.lower(), [])) > 1:
        issue("duplicate_doi", "warning", "Another item has the same DOI", others=[k for k in ctx.doi_map[doi.lower()] if k != key],
              fp_extra=doi.lower())
    if typ in ("journalArticle", "conferencePaper") and not doi:
        issue("missing_doi", "info", "No DOI (a Crossref search may find one)")
    if typ in ("webpage", "document", "blogPost") and doi:
        issue("type_suspect", "warning", f"{typ} with a DOI — probably a scholarly work (run lookups for a type suggestion)")
    if d.get("ISBN") and not all(tu.isbn_valid(x) for x in tu.normalize_isbn_field(d["ISBN"]).split()):
        issue("invalid_isbn", "warning", f"ISBN checksum invalid: {d['ISBN']}")
    if d.get("ISSN") and not all(tu.issn_valid(x) for x in re.findall(r"\d{4}-?\d{3}[\dXx]", d["ISSN"])):
        issue("invalid_issn", "warning", f"ISSN checksum invalid: {d['ISSN']}")
    if ctx.latex_on:
        for f in ("title", "shortTitle", pub_f or "publicationTitle"):
            val = cur(f) if f else ""
            if val and ctx.is_latex_field(typ, f):
                probs = latex.check(val)
                if probs:
                    issue("latex_error", "error", f"{f}: " + "; ".join(probs), field=f, fp_extra=f)
                um = latex.unmapped_chars(val)
                if um and any(not ("Ͱ" <= ch <= "Ͽ") for ch in um):
                    issue("latex_unmapped", "info", f"{f}: characters without a LaTeX equivalent: {' '.join(um[:10])}",
                          field=f, fp_extra=f)
    return props, issues


def run_rules(ctx: Context, items: list[dict] | None = None):
    props, issues = [], []
    for it in (items if items is not None else ctx.regular):
        if it["itemType"] in NON_REGULAR or it["deleted"]:
            continue
        p, i = item_proposals(ctx, it)
        props += p
        issues += i
    return props, issues
