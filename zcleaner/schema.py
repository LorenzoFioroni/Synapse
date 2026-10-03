"""Zotero item-type schema: which fields exist for which item type, and how
type-specific fields map to base fields (e.g. proceedingsTitle -> publicationTitle)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)

# Minimal fallback used only if the schema cannot be downloaded.
FALLBACK_BASE = {
    "bookSection": {"publicationTitle": "bookTitle"},
    "conferencePaper": {"publicationTitle": "proceedingsTitle"},
    "encyclopediaArticle": {"publicationTitle": "encyclopediaTitle"},
    "dictionaryEntry": {"publicationTitle": "dictionaryTitle"},
    "webpage": {"publicationTitle": "websiteTitle", "type": "websiteType"},
    "blogPost": {"publicationTitle": "blogTitle", "type": "websiteType"},
    "forumPost": {"publicationTitle": "forumTitle", "type": "postType"},
    "thesis": {"publisher": "university", "type": "thesisType"},
    "report": {"publisher": "institution", "type": "reportType", "number": "reportNumber"},
    "preprint": {"publisher": "repository", "type": "genre", "number": "archiveID"},
    "patent": {"number": "patentNumber", "date": "issueDate"},
    "film": {"publisher": "distributor"},
    "audioRecording": {"publisher": "label"},
    "computerProgram": {"publisher": "company"},
}


class Schema:
    def __init__(self, cache_dir: Path):
        self.cache = cache_dir / "zotero_schema.json"
        self.types: dict[str, dict] = {}
        self.loaded = False

    # ------------------------------------------------------------------
    def load(self, local_api_base: str | None = None, force: bool = False) -> bool:
        data = None
        if self.cache.exists() and not force:
            try:
                data = json.loads(self.cache.read_text())
            except Exception:
                data = None
        if data is None:
            for url in ([local_api_base.rstrip("/") + "/schema"] if local_api_base else []) + ["https://api.zotero.org/schema"]:
                try:
                    r = requests.get(url, timeout=20, headers={"Zotero-API-Version": "3"})
                    if r.status_code == 200 and "itemTypes" in r.text[:2000]:
                        data = r.json()
                        self.cache.write_text(json.dumps(data))
                        break
                except Exception as e:  # pragma: no cover - network
                    log.info("schema fetch %s failed: %s", url, e)
        if data:
            self._ingest(data)
            self.loaded = True
        return self.loaded

    def _ingest(self, data: dict):
        for t in data.get("itemTypes", []):
            fields = {}
            for f in t.get("fields", []):
                fields[f["field"]] = f.get("baseField")
            self.types[t["itemType"]] = {
                "fields": fields,
                "base": {b: f for f, b in fields.items() if b},
                "creators": [c["creatorType"] for c in t.get("creatorTypes", [])],
                "primary": next((c["creatorType"] for c in t.get("creatorTypes", []) if c.get("primary")), "author"),
            }

    # ------------------------------------------------------------------
    def item_types(self) -> list[str]:
        return sorted(self.types) if self.types else []

    def has_field(self, item_type: str, field: str) -> bool:
        if field in ("itemType", "creators", "tags", "collections", "relations", "extra", "title",
                     "abstractNote", "accessDate", "dateAdded", "dateModified", "key", "version",
                     "deleted", "parentItem", "note", "inPublications"):
            return True
        if not self.types:
            return True  # unknown -> let Zotero validate
        t = self.types.get(item_type)
        return bool(t) and field in t["fields"]

    def actual(self, item_type: str, base: str) -> str | None:
        """Name of the field used for *base* in *item_type* (or None)."""
        if self.types:
            t = self.types.get(item_type)
            if not t:
                return None
            if base in t["fields"]:
                return base
            return t["base"].get(base)
        return FALLBACK_BASE.get(item_type, {}).get(base, base)

    def base_of(self, item_type: str, field: str) -> str:
        if self.types:
            t = self.types.get(item_type)
            if t and t["fields"].get(field):
                return t["fields"][field]
            return field
        for b, f in FALLBACK_BASE.get(item_type, {}).items():
            if f == field:
                return b
        return field

    def creator_types(self, item_type: str) -> list[str]:
        t = self.types.get(item_type)
        return t["creators"] if t else ["author", "editor", "contributor"]

    def fields(self, item_type: str) -> list[str]:
        t = self.types.get(item_type)
        return list(t["fields"]) if t else []

    def convert_type(self, data: dict, new_type: str) -> tuple[dict, dict]:
        """Return (new_data, dropped) for changing an item's type.

        *new_data* is a complete item JSON (for a PUT).  Fields are moved via
        their base field; fields without a home in the new type are returned in
        *dropped* and appended to Extra by the caller, so no data is lost."""
        old_type = data.get("itemType")
        keep = {"key", "version", "creators", "tags", "collections", "relations",
                "dateAdded", "dateModified", "extra", "deleted", "inPublications", "parentItem"}
        new = {k: v for k, v in data.items() if k in keep}
        new["itemType"] = new_type
        dropped: dict = {}
        for f, v in data.items():
            if f in keep or f == "itemType" or v in ("", None, [], {}):
                continue
            base = self.base_of(old_type, f)
            target = self.actual(new_type, base)
            if target and self.has_field(new_type, target) and not new.get(target):
                new[target] = v
            elif new_type == "preprint" and base == "publicationTitle" and str(v).lower().startswith("arxiv"):
                continue  # 'arXiv preprint arXiv:…' pseudo-journal: replaced by repository/archiveID
            else:
                dropped[f] = v
        allowed = self.creator_types(new_type)
        primary = self.types.get(new_type, {}).get("primary", "author")
        cr = []
        for c in data.get("creators", []) or []:
            if allowed and c.get("creatorType") not in allowed:
                c = dict(c, creatorType=primary if c.get("creatorType") in ("author", "inventor", "programmer", "artist", "director", "presenter", "podcaster", "performer", "cartographer", "interviewee", "sponsor") else ("contributor" if "contributor" in allowed else primary))
            cr.append(c)
        new["creators"] = cr
        if dropped:
            lines = [ln for ln in (new.get("extra") or "").splitlines() if ln.strip()]
            for f, v in dropped.items():
                if f == "archiveID" and str(v).lower().startswith("arxiv:"):
                    ln = f"arXiv: {str(v)[6:].strip()}"
                elif f in ("repository", "publisher") and str(v).strip().lower() == "arxiv":
                    continue  # implied by the arXiv line
                else:
                    ln = f"{f}: {v}"
                if ln not in lines:
                    lines.append(ln)
            new["extra"] = "\n".join(lines)
        return new, dropped
