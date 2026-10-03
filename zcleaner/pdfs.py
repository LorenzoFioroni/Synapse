"""PDF attachment checks and file-name normalisation proposals."""
from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

from . import filenames
from . import textutil as tu

log = logging.getLogger(__name__)

try:  # PyMuPDF is faster and extracts text better; optional
    import fitz  # type: ignore
except Exception:  # pragma: no cover
    fitz = None
try:
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover
    PdfReader = None

PDF_TYPES = ("application/pdf",)


def is_pdf_attachment(d: dict) -> bool:
    return d.get("contentType") in PDF_TYPES or (d.get("filename") or d.get("path") or "").lower().endswith(".pdf")


def analyze_pdf(path: Path, max_pages: int = 2) -> dict:
    res = {"is_pdf": 0, "pages": None, "has_text": None, "encrypted": 0, "text": "", "error": None}
    try:
        with open(path, "rb") as f:
            head = f.read(2048)
    except OSError as e:
        res["error"] = str(e)
        return res
    if b"%PDF-" not in head[:1024]:
        low = head.lower()
        res["error"] = "HTML page saved as PDF" if (b"<html" in low or b"<!doctype" in low) else "not a PDF file"
        return res
    res["is_pdf"] = 1
    text = ""
    try:
        if fitz is not None:
            doc = fitz.open(path)
            res["encrypted"] = int(bool(doc.needs_pass))
            res["pages"] = doc.page_count
            if not doc.needs_pass:
                for i in range(min(max_pages, doc.page_count)):
                    text += doc[i].get_text() + "\n"
                meta = doc.metadata or {}
                res["meta_title"] = meta.get("title") or ""
                res["meta_subject"] = meta.get("subject") or ""
            doc.close()
        elif PdfReader is not None:
            r = PdfReader(str(path), strict=False)
            if r.is_encrypted:
                res["encrypted"] = 1
                try:
                    r.decrypt("")
                except Exception:
                    pass
            res["pages"] = len(r.pages)
            for i in range(min(max_pages, len(r.pages))):
                try:
                    text += (r.pages[i].extract_text() or "") + "\n"
                except Exception:
                    pass
            try:
                md = r.metadata or {}
                res["meta_title"] = str(md.get("/Title") or "")
                res["meta_subject"] = str(md.get("/Subject") or "")
            except Exception:
                pass
        else:
            res["error"] = "no PDF library installed (pip install pypdf)"
            return res
    except Exception as e:
        res["error"] = f"unreadable PDF: {e}"[:300]
        return res
    res["text"] = text[:8000]
    res["has_text"] = int(len(re.sub(r"\s+", "", text)) >= 80)
    return res


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_data_dir(zot, atts: list[dict]) -> str | None:
    for a in atts:
        if a["data"].get("linkMode") in ("imported_file", "imported_url"):
            try:
                p = zot.file_path(a["key"])
            except Exception:
                continue
            if p and len(p.parents) >= 3 and p.parent.parent.name == "storage":
                return str(p.parent.parent.parent)
    return None


def resolve_path(zot, data_dir: str | None, att: dict) -> Path | None:
    d = att["data"]
    mode = d.get("linkMode")
    if mode in ("imported_file", "imported_url"):
        if data_dir and d.get("filename"):
            return Path(data_dir) / "storage" / att["key"] / d["filename"]
        return zot.file_path(att["key"])
    if mode == "linked_file":
        p = d.get("path") or ""
        if p and not p.startswith("attachments:"):
            return Path(p)
        return zot.file_path(att["key"])
    return None


def scan(settings, store, zot, progress=None, cancelled=None, only_parents: set | None = None):
    """Check attachments; returns (issues, rename_proposals, data_dir)."""
    items = store.items("deleted=0")
    by_key = {i["key"]: i for i in items}
    atts = [i for i in items if i["itemType"] == "attachment"]
    data_dir = settings.get("data_dir") or store.meta_get("data_dir")
    if not data_dir:
        data_dir = detect_data_dir(zot, atts)
        if data_dir:
            store.meta_set("data_dir", data_dir)
    previous = {r["att_key"]: r for r in store.pdf_rows()}
    children = defaultdict(list)
    for a in atts:
        if a["parent"]:
            children[a["parent"]].append(a)
    cfg = settings.get("pdf")
    need_types = set(cfg.get("types_needing_pdf") or [])
    optional_types = set(cfg.get("types_optional_pdf") or [])
    issues, renames = [], []
    pdf_atts = [a for a in atts if is_pdf_attachment(a["data"]) and (only_parents is None or a["parent"] in only_parents or a["key"] in only_parents)]
    results = {}
    for n, a in enumerate(pdf_atts):
        if cancelled and cancelled():
            break
        if progress and n % 20 == 0:
            progress(n, len(pdf_atts))
        d = a["data"]
        row = {"att_key": a["key"], "parent_key": a["parent"], "link_mode": d.get("linkMode"), "md5": d.get("md5")}
        try:
            path = resolve_path(zot, data_dir, a)
        except Exception as e:
            path, row["error"] = None, str(e)
        row["path"] = str(path) if path else None
        if d.get("linkMode") == "linked_url":
            row["exists_"] = None
        elif not path or not path.exists():
            row["exists_"] = 0
        else:
            st = path.stat()
            row.update(exists_=1, size=st.st_size, mtime=st.st_mtime)
            prev = previous.get(a["key"])
            if prev and prev["path"] == row["path"] and prev["size"] == st.st_size and prev["mtime"] == st.st_mtime and prev["checked"]:
                for k in ("is_pdf", "pages", "has_text", "encrypted", "found_doi", "first_text", "error"):
                    row[k] = prev[k]
                row["md5"] = row["md5"] or prev["md5"]
            elif st.st_size == 0:
                row.update(is_pdf=0, error="empty file (0 bytes)")
            else:
                r = analyze_pdf(path) if cfg.get("verify_content", True) else {"is_pdf": 1}
                row.update(is_pdf=r["is_pdf"], pages=r.get("pages"), has_text=r.get("has_text"),
                           encrypted=r.get("encrypted"), error=r.get("error"))
                text = r.get("text") or ""
                row["first_text"] = text[:3000]
                row["found_doi"] = tu.find_doi(text) or tu.find_doi(r.get("meta_subject", "")) or None
                if not row["md5"] and d.get("linkMode") == "linked_file":
                    try:
                        row["md5"] = md5_file(path)
                    except OSError:
                        pass
        # title agreement (computed every time: the parent title may have changed)
        parent = by_key.get(a["parent"]) if a["parent"] else None
        if parent and row.get("first_text"):
            t = tu.canon(parent["data"].get("title", ""))
            row["title_score"] = fuzz.partial_ratio(t[:120], tu.canon(row["first_text"])) if t else None
        store.pdf_upsert(row)
        results[a["key"]] = row
    if progress:
        progress(len(pdf_atts), len(pdf_atts))

    tags = settings.get("tags")
    # ---- per parent
    parents = [i for i in items if i["itemType"] not in ("note", "attachment", "annotation")]
    if only_parents is not None:
        parents = [p for p in parents if p["key"] in only_parents]
    md5_seen = defaultdict(list)
    for r in results.values():
        if r.get("md5") and r.get("exists_"):
            md5_seen[r["md5"]].append(r["att_key"])
    attach_open = {x["item_key"] for x in store.proposals("kind='attach_pdf' AND status IN ('pending','accepted')")}

    def not_found_note(key):
        r = store.cache_get("pdffind:" + key)
        if r and not r.get("found"):
            return f" — {r.get('reason') or 'no open-access copy found online'}"
        return ""

    for p in parents:
        kids = children.get(p["key"], [])
        pdfs = [k for k in kids if is_pdf_attachment(k["data"])]
        d = p["data"]

        def issue(code, sev, msg, **det):
            issues.append({"item_key": p["key"], "code": code, "severity": sev, "message": msg, "details": det,
                           "source": "pdf", "fp_extra": det.get("att_key", "")})
        if not pdfs:
            if p["key"] in attach_open:
                continue  # a PDF was found online: the fix is waiting in Review
            if p["itemType"] in need_types:
                other = [k["data"].get("linkMode") for k in kids]
                msg = "No PDF attached"
                if "linked_url" in other:
                    msg += " (only a web link)"
                elif other:
                    msg += " (has other attachments: snapshot/other files)"
                msg += not_found_note(p["key"])
                doi = tu.clean_doi(d.get("DOI", ""))
                sev = "info" if p["itemType"] in optional_types else "warning"
                if sev == "info":
                    msg += f" (fine for a {p['itemType']})"
                issue("missing_pdf", sev, msg, doi=doi, arxiv=tu.arxiv_from(" ".join([d.get("url", ""), d.get("extra", ""), d.get("DOI", ""), d.get("archiveID", "")])),
                      url=d.get("url", ""), tag=tags.get("needs_pdf"))
            continue
        good = 0
        for k in pdfs:
            r = results.get(k["key"]) or previous.get(k["key"]) or {}
            fname = k["data"].get("filename") or Path(k["data"].get("path") or "").name
            if r.get("exists_") == 0:
                issue("pdf_file_missing", "error",
                      f"PDF attachment “{fname}” has no file on this computer" +
                      (" (linked file moved/deleted?)" if k["data"].get("linkMode") == "linked_file" else " (not downloaded / deleted?)"),
                      att_key=k["key"], path=r.get("path"))
                continue
            if r.get("error") and not r.get("is_pdf"):
                code = "pdf_empty" if "0 bytes" in (r.get("error") or "") else "pdf_not_pdf"
                issue(code, "error", f"“{fname}”: {r['error']}", att_key=k["key"], path=r.get("path"))
                continue
            if r.get("error"):
                issue("pdf_unreadable", "warning", f"“{fname}”: {r['error']}", att_key=k["key"], path=r.get("path"))
            good += 1
            if r.get("encrypted"):
                issue("pdf_encrypted", "info", f"“{fname}” is password-protected/encrypted", att_key=k["key"], path=r.get("path"))
            if r.get("has_text") == 0:
                issue("pdf_no_text", "info", f"“{fname}” has no text layer (scanned) — run OCR", att_key=k["key"], path=r.get("path"))
            item_doi = tu.clean_doi(d.get("DOI", "")).lower()
            fd = (r.get("found_doi") or "").lower()
            mismatch = False
            if item_doi and fd and fd != item_doi and not fd.startswith(item_doi) and not item_doi.startswith(fd) \
                    and (r.get("title_score") or 0) < 80:
                mismatch = True
                issue("pdf_mismatch", "warning", f"“{fname}” mentions DOI {fd}, the item has {item_doi}",
                      att_key=k["key"], path=r.get("path"), found_doi=fd)
            elif r.get("title_score") is not None and r["title_score"] < 45 and r.get("has_text") and not fd:
                mismatch = True
                issue("pdf_mismatch", "warning", f"“{fname}”: the item title was not found on the first pages (score {r['title_score']:.0f})",
                      att_key=k["key"], path=r.get("path"))
            if r.get("md5") and len(md5_seen.get(r["md5"], [])) > 1:
                others = [x for x in md5_seen[r["md5"]] if x != k["key"]]
                issue("pdf_duplicate_file", "info", f"“{fname}” is identical to another attachment", att_key=k["key"],
                      others=others)
            # rename proposal
            if cfg.get("rename", True) and r.get("exists_") and not mismatch and (k["data"].get("linkMode") != "linked_file" or cfg.get("rename_linked", True)):
                cur = k["data"].get("filename") or Path(k["data"].get("path") or "").name
                want = filenames.filename_for(d, p.get("meta"), cur, settings)
                if cur and want != cur:
                    renames.append({"item_key": k["key"], "kind": "rename_file", "field": "filename", "old": cur, "new": want,
                                    "rules": ["pdf_name"], "source": "pdf", "confidence": 0.9,
                                    "note": f"parent {p['key']} · {k['data'].get('linkMode')}"})
        if good == 0 and pdfs and p["itemType"] in need_types:
            issue("missing_pdf", "info" if p["itemType"] in optional_types else "warning",
                  "No usable PDF (attachments are missing or broken)", doi=tu.clean_doi(d.get("DOI", "")),
                  tag=tags.get("needs_pdf"))
        if good > 1:
            issue("pdf_multiple", "info", f"{good} PDFs attached", count=good)
    # ---- orphans
    for a in atts:
        if a["parent"] or not is_pdf_attachment(a["data"]):
            continue
        if only_parents is not None and a["key"] not in only_parents:
            continue
        r = results.get(a["key"]) or {}
        issues.append({"item_key": a["key"], "code": "orphan_pdf", "severity": "warning",
                       "message": "Standalone PDF without a parent item" + (f" (contains DOI {r['found_doi']})" if r.get("found_doi") else ""),
                       "details": {"found_doi": r.get("found_doi"), "path": r.get("path")}, "source": "pdf"})
    return issues, renames, data_dir
