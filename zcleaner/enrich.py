"""Compare external records with library items and turn differences into
reviewable proposals.  Nothing is overwritten blindly:

* if the DOI resolves to a *different* work, an issue is raised instead of
  proposing to overwrite the item with someone else's metadata;
* dates are never made less precise; richer author names are kept;
* all incoming values pass through the same normalisation pipeline (LaTeX,
  whitespace, markup) as local rules.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from . import latex
from . import textutil as tu
from .rules import normalize_creators, normalize_value

SCHOLARLY = {"journalArticle", "conferencePaper", "bookSection", "book", "preprint", "thesis", "report"}
WEAK_TYPES = {"webpage", "document", "blogPost", "manuscript", "forumPost"}
FIELDS = ["title", "publicationTitle", "journalAbbreviation", "volume", "issue", "pages", "date", "ISSN",
          "ISBN", "publisher", "place", "conferenceName", "repository", "archiveID", "abstractNote", "DOI", "url"]
FILL_ONLY = {"abstractNote", "publisher", "place", "url", "DOI", "ISBN", "conferenceName", "repository", "archiveID", "language"}


def title_match(a: str, b: str) -> float:
    ca, cb = tu.canon(a), tu.canon(b)
    if not ca or not cb:
        return 0.0
    r = fuzz.ratio(ca, cb)
    # a truncated/extended title (subtitle missing) still matches well
    if min(len(ca), len(cb)) >= 25:
        r = max(r, fuzz.partial_ratio(ca, cb) - 5)
    return r


def first_last(creators) -> str:
    for c in creators or []:
        if c.get("creatorType", "author") in ("author", "inventor", "programmer", "artist", "director", "presenter"):
            return tu.canon(c.get("lastName") or c.get("name") or "").split(" ")[-1] if (c.get("lastName") or c.get("name")) else ""
    return ""


def _richer_first(a: str, b: str) -> str:
    """Keep the richer of two first names ('J.' vs 'John' -> 'John')."""
    ua, ub = tu.plain(a), tu.plain(b)
    if not ua:
        return b
    if not ub:
        return a
    ca, cb = tu.canon(ua), tu.canon(ub)
    if ca == cb and _shouting(a) and not _shouting(b):
        return b
    if ca == cb:
        # identical modulo accents/case: prefer the one with accents / proper case
        return b if (len(tu.strip_accents(ua)) == len(ua) and len(tu.strip_accents(ub)) != len(ub)) else a
    initials_a = re.fullmatch(r"(?:[A-Z]\.?[\s-]*)+", ua) is not None
    initials_b = re.fullmatch(r"(?:[A-Z]\.?[\s-]*)+", ub) is not None
    if initials_a and not initials_b and cb[:1] == ca[:1]:
        return b
    if cb.startswith(ca + " "):
        return b  # 'Soo' -> 'Soo-Min', 'John' -> 'John R.'
    return a


def _shouting(s: str) -> bool:
    p = [c for c in tu.plain(s) if c.isalpha()]
    return len(p) >= 2 and all(c.isupper() for c in p)


def _has_accents(s: str) -> bool:
    p = tu.plain(s)
    return tu.strip_accents(p) != p


def merge_creators(cur: list[dict], ext: list[dict]) -> tuple[list[dict], float, str]:
    """Merge external authors into current creators. Returns (merged, confidence, note)."""
    authors_c = [c for c in cur if c.get("creatorType") == "author"]
    others_c = [c for c in cur if c.get("creatorType") != "author"]
    authors_e = [c for c in ext if c.get("creatorType") == "author"]
    editors_e = [c for c in ext if c.get("creatorType") == "editor"]
    if not authors_e:
        return cur, 1.0, ""
    if not authors_c:
        return authors_e + others_c + ([] if others_c else editors_e), 0.85, "authors were missing"
    merged, mismatches = [], 0
    for i, e in enumerate(authors_e):
        c = authors_c[i] if i < len(authors_c) else None
        if c is None:
            merged.append(e)
            continue
        lc = tu.canon(c.get("lastName") or c.get("name") or "")
        le = tu.canon(e.get("lastName") or e.get("name") or "")
        if lc == le or (lc and le and (lc in le or le in lc)):
            if "name" in c and "lastName" not in c:
                merged.append(c)
                continue
            last = c.get("lastName", "")
            el = e.get("lastName", "")
            if tu.canon(last) == tu.canon(el) and ((_has_accents(el) and not _has_accents(last)) or (_shouting(last) and not _shouting(el))):
                last = el  # the source has the proper accents / capitalisation
            merged.append({"creatorType": "author", "firstName": _richer_first(c.get("firstName", ""), e.get("firstName", "")),
                           "lastName": last})
        else:
            mismatches += 1
            merged.append(e)
    note, conf = "", 0.75
    if len(authors_e) < len(authors_c):
        # external list is shorter -> keep the extra local authors
        merged += authors_c[len(authors_e):]
        note, conf = "the source lists fewer authors; extra local authors kept", 0.55
    elif len(authors_e) > len(authors_c):
        note, conf = f"adds {len(authors_e) - len(authors_c)} missing author(s)", 0.7
    if mismatches:
        note = (note + "; " if note else "") + f"{mismatches} author name(s) differ"
        conf = min(conf, 0.45 if mismatches > 1 else 0.55)
    return merged + others_c, conf, note


def _date_choice(cur: str, cur_parsed: str, ext: str):
    """Return (value, conf, note) or None when the current date should stay."""
    if not ext:
        return None
    if not cur:
        return ext, 0.9, ""
    cy = (cur_parsed or re.search(r"\d{4}", cur).group(0) if re.search(r"\d{4}", cur) else "")[:4]
    if cy == ext[:4]:
        cur_prec = len(cur_parsed) if cur_parsed else (4 if re.fullmatch(r"\d{4}", cur.strip()) else 10)
        if len(ext) > cur_prec:
            return ext, 0.75, "more precise date"
        return None
    return ext, 0.5, f"year differs ({cy} vs {ext[:4]}) — print vs online date?"


def compare(ctx, it: dict, rec: dict, *, via: str, matched_by_search: float | None = None,
            group: str | None = None, target_type: str | None = None, publishing: bool = False) -> tuple[list[dict], list[dict]]:
    """Proposals/issues from an external record *rec* for item *it*."""
    d, key, typ = it["data"], it["key"], it["itemType"]
    props, issues = [], []
    src = rec.get("source", via)

    def issue(code, sev, msg, **details):
        issues.append({"item_key": key, "code": code, "severity": sev, "message": msg, "details": details,
                       "source": "lookup"})

    # ---- is it the same work?
    tm = title_match(d.get("title", ""), rec.get("title", "")) if d.get("title") else 100
    fa_c, fa_e = first_last(d.get("creators")), first_last(rec.get("creators"))
    author_ok = not fa_c or not fa_e or fa_c == fa_e or fuzz.ratio(fa_c, fa_e) > 85
    if matched_by_search is None and (tm < 60 or (tm < 80 and not author_ok)):
        issue("doi_mismatch", "error",
              f"The DOI resolves to a different work: “{tu.plain(rec.get('title', ''))[:150]}” ({src})",
              their_title=rec.get("title"), their_authors=[tu.plain(c.get("lastName") or c.get("name") or "") for c in rec.get("creators", [])][:5],
              their_date=rec.get("date"), title_similarity=round(tm), fp_extra=rec.get("DOI", ""))
        return props, issues

    base_conf = 0.95 if matched_by_search is None else min(0.8, matched_by_search / 100 - 0.1)
    grp = group or (f"match-{key}" if matched_by_search is not None else None)
    note_src = f"from {src}" + (f" (search match {matched_by_search:.0f}%)" if matched_by_search is not None else "")

    # ---- item type
    new_type = target_type
    ext_type = rec.get("itemType")
    if not new_type and ext_type and ext_type != typ and (not ctx.schema.types or ext_type in ctx.schema.types):
        if typ in WEAK_TYPES or (typ, ext_type) in {("journalArticle", "conferencePaper"), ("conferencePaper", "journalArticle"),
                                                     ("report", "journalArticle"), ("journalArticle", "preprint"),
                                                     ("book", "bookSection"), ("bookSection", "book")}:
            if not (typ == "journalArticle" and ext_type == "preprint" and d.get("publicationTitle")):
                new_type = ext_type
    published_known = bool(rec.get("published_doi") or rec.get("journal_ref") or rec.get("is_preprint_of"))
    if new_type == "preprint" and published_known and not target_type:
        new_type = None  # arXiv itself says this preprint has been published: never demote to preprint
    if new_type and new_type != typ and not ctx.type_change_allowed(key, new_type, publishing):
        new_type = None  # you already decided this item's type (applied or rejected): no flip-flopping
    data_for_fields = d
    type_group = grp
    if new_type and new_type != typ:
        new_data, dropped = ctx.schema.convert_type(d, new_type)
        type_group = grp or f"type-{key}"
        props.append({"item_key": key, "kind": "itemType", "field": "itemType", "old": typ,
                      "new": {"itemType": new_type, "data": {k: v for k, v in new_data.items() if k not in ("key", "version")},
                              "dropped": dropped},
                      "rules": ["item_type"], "source": src, "confidence": 0.6 if typ in WEAK_TYPES else 0.5,
                      "note": f"{src} says this is a {new_type}", "group_id": type_group})
        data_for_fields = new_data
    eff_type = new_type or typ

    # ---- fields
    for base in FIELDS:
        ext = rec.get(base)
        if not ext or not isinstance(ext, str):
            continue
        field = ctx.schema.actual(eff_type, base)
        if not field or not ctx.schema.has_field(eff_type, field):
            continue
        if base == "url":
            if data_for_fields.get("url"):
                continue
            if rec.get("DOI") and src != "arxiv":
                ext = "https://doi.org/" + rec["DOI"]
        if base == "abstractNote":
            ext = tu.plain(ext) if not ctx.is_latex_field(eff_type, "abstractNote") else ext
            ext = re.sub(r"^\s*(Abstract|Summary)[.:]?\s*", "", ext, flags=re.I)
        if base == "DOI" and src == "arxiv" and eff_type != "preprint":
            continue
        new, rules, conf, notes = normalize_value(ctx, eff_type, field, ext, external=True)
        cur = data_for_fields.get(field) or ""
        if base == "date":
            ch = _date_choice(cur, (it.get("meta") or {}).get("parsedDate", ""), new)
            if not ch:
                continue
            new, c, n = ch
            props.append(_p(key, field, d.get(field, ""), new, src, min(base_conf, c), "; ".join(x for x in (note_src, n) if x), type_group))
            continue
        if not cur:
            props.append(_p(key, field, d.get(field, ""), new, src, min(base_conf, 0.9), note_src, type_group))
            continue
        if base in FILL_ONLY:
            continue
        cur_n = normalize_value(ctx, eff_type, field, cur)[0]
        if tu.loose_equal(cur_n, new) or cur_n == new:
            continue
        if base == "ISSN" and all(x in cur for x in re.findall(r"\d{4}-\d{3}[\dX]", new)):
            continue
        if base == "pages" and tu.canon(cur_n).replace(" ", "") == tu.canon(new).replace(" ", ""):
            continue
        if base == "title":
            s = title_match(cur_n, new)
            c = 0.6 if s >= 90 else 0.4
            n = "title differs slightly" if s >= 90 else "title differs"
        elif base == "publicationTitle":
            c, n = 0.55, "journal/proceedings name differs"
        elif base == "journalAbbreviation":
            c, n = 0.5, "abbreviation differs"
        else:
            c, n = 0.7, ""
        props.append(_p(key, field, d.get(field, ""), new, src, min(base_conf, c), "; ".join(x for x in (note_src, n) if x), type_group))

    # ---- creators
    ext_cr = rec.get("creators") or []
    if ext_cr:
        ext_cr, _, _, _ = normalize_creators(ctx, eff_type, ext_cr, external=True)
        cur_cr = d.get("creators") or []
        merged, c, n = merge_creators(cur_cr, ext_cr)
        allowed = set(ctx.schema.creator_types(eff_type))
        merged = [m if m.get("creatorType") in allowed or not allowed else dict(m, creatorType="contributor" if "contributor" in allowed else "author") for m in merged]
        if [_cn(x) for x in merged] != [_cn(x) for x in cur_cr]:
            props.append({"item_key": key, "kind": "creators", "field": "creators", "old": cur_cr, "new": merged,
                          "rules": [], "source": src, "confidence": round(min(base_conf, c), 2),
                          "note": "; ".join(x for x in (note_src, n) if x), "group_id": type_group})

    # ---- preprint -> published version
    pub = [x for x in rec.get("is_preprint_of", []) if x] + ([rec["published_doi"]] if rec.get("published_doi") else [])
    item_doi = tu.clean_doi(d.get("DOI", "")).lower()
    if pub and pub[0].lower() != item_doi and (typ == "preprint" or not item_doi or item_doi.startswith("10.48550/")):
        issue("published_version", "info", f"A published version exists: {pub[0]}", doi=pub[0],
              journal_ref=rec.get("journal_ref", ""), fp_extra=pub[0])
    return props, issues


def _cn(c):
    return (c.get("creatorType"), tu.plain(c.get("lastName") or c.get("name") or ""), tu.plain(c.get("firstName") or ""),
            c.get("lastName", "") + c.get("firstName", "") + c.get("name", ""))


def _p(key, field, old, new, src, conf, note, group):
    return {"item_key": key, "kind": "field", "field": field, "old": old, "new": new, "rules": [],
            "source": src, "confidence": round(conf, 2), "note": note, "group_id": group}


def search_score(it: dict, rec: dict) -> float:
    """0..100 match score for a search candidate."""
    d = it["data"]
    t = title_match(d.get("title", ""), rec.get("title", ""))
    if t < 85:
        return t * 0.8
    score = t
    fa_c, fa_e = first_last(d.get("creators")), first_last(rec.get("creators"))
    if fa_c and fa_e:
        score += 5 if fa_c == fa_e else -20
    else:
        score -= 5
    cy = re.search(r"\d{4}", d.get("date", "") or "")
    ey = rec.get("date", "")[:4]
    if cy and ey:
        dy = abs(int(cy.group(0)) - int(ey))
        score += 3 if dy == 0 else (-3 if dy == 1 else -25)
    else:
        score -= 3
    return max(0.0, min(100.0, score))


def item_ids(it: dict) -> tuple[str, str]:
    d = it["data"]
    doi = tu.clean_doi(d.get("DOI", ""))
    if not doi:
        m = re.search(r"^\s*DOI:\s*(\S+)", d.get("extra", "") or "", re.I | re.M)
        if m:
            doi = tu.clean_doi(m.group(1))
        elif re.search(r"doi\.org/10\.", d.get("url", "") or "", re.I):
            doi = tu.clean_doi(d["url"])
    if doi and not tu.DOI_STRICT.match(doi):
        doi = ""
    ax = tu.arxiv_from(" ".join(str(d.get(f, "")) for f in ("archiveID", "url", "extra", "DOI", "publicationTitle", "repository")))
    return doi, ax
