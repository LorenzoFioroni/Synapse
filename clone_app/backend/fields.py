"""Single source of truth for item fields and item types.

The database schema, the API whitelist, the search scopes and the item pane
in the browser are all derived from these definitions (the frontend fetches
them from ``GET /api/schema``).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    sql: str = "TEXT"
    multiline: bool = False
    editable: bool = True
    basic_search: bool = False     # part of the "Title, Abstract & Authors" scope
    searchable: bool = True        # part of the "All Fields" scope
    rare: bool = False             # hidden in the item pane while empty


FIELDS: list[Field] = [
    Field("item_type", "Item Type", searchable=False),
    Field("title", "Title", multiline=True, basic_search=True),
    Field("authors", "Authors", basic_search=True),
    Field("year", "Year", sql="INTEGER"),
    Field("journal", "Publication"),
    Field("volume", "Volume"),
    Field("issue", "Issue"),
    Field("pages", "Pages"),
    Field("publisher", "Publisher"),
    Field("place", "Place"),
    Field("edition", "Edition"),
    Field("series", "Series"),
    Field("isbn", "ISBN"),
    Field("issn", "ISSN"),
    Field("doi", "DOI"),
    Field("url", "URL"),
    Field("citation_key", "Citation Key"),
    Field("short_title", "Short Title"),
    Field("language", "Language"),
    Field("keywords", "Keywords"),
    Field("abstract", "Abstract", multiline=True, basic_search=True),
    Field("extra", "Extra", multiline=True),
    Field("pmid", "PMID", rare=True),
    Field("pmcid", "PMCID", rare=True),
    Field("archive", "Archive", rare=True),
    Field("archive_id", "Archive ID", rare=True),
    Field("library_catalog", "Library Catalog", rare=True),
    Field("call_number", "Call Number", rare=True),
    Field("license", "License", rare=True),
    Field("date_added", "Date Added", editable=False, searchable=False),
    Field("date_modified", "Date Modified", editable=False, searchable=False),
]

FIELD_MAP = {f.key: f for f in FIELDS}
EDITABLE_KEYS = [f.key for f in FIELDS if f.editable]
BASIC_SEARCH_KEYS = [f.key for f in FIELDS if f.basic_search]
ALL_SEARCH_KEYS = [f.key for f in FIELDS if f.searchable]

# Columns that exist in the table but are not user-editable metadata.
INTERNAL_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "annotations": "TEXT",
    "pdfUrl": "TEXT",
    "deleted_at": "TEXT",
    "folder_id": "INTEGER",   # an item lives in at most one folder (NULL = unfiled)
}

ITEM_TYPES = [
    ("journalArticle", "Journal Article"),
    ("book", "Book"),
    ("bookSection", "Book Section"),
    ("conferencePaper", "Conference Paper"),
    ("thesis", "Thesis"),
    ("report", "Report"),
    ("preprint", "Preprint"),
    ("manuscript", "Manuscript"),
    ("webpage", "Web Page"),
    ("dataset", "Dataset"),
]
ITEM_TYPE_LABELS = dict(ITEM_TYPES)
DEFAULT_ITEM_TYPE = "journalArticle"

# Type-specific fields shown between Authors and the common block, and the
# label the "journal" (container) field takes for that type.
TYPE_FIELDS: dict[str, list[str]] = {
    "journalArticle": ["journal", "volume", "issue", "pages", "issn"],
    "book": ["publisher", "place", "edition", "series", "volume", "isbn"],
    "bookSection": ["journal", "publisher", "place", "edition", "pages", "isbn"],
    "conferencePaper": ["journal", "publisher", "place", "pages", "series"],
    "thesis": ["publisher", "place"],
    "report": ["journal", "publisher", "place", "series", "pages"],
    "preprint": ["journal", "archive_id"],
    "manuscript": ["place"],
    "webpage": ["journal"],
    "dataset": ["publisher", "archive", "archive_id"],
}
CONTAINER_LABELS = {
    "journalArticle": "Publication",
    "bookSection": "Book Title",
    "conferencePaper": "Proceedings Title",
    "report": "Institution",
    "preprint": "Repository",
    "webpage": "Website Title",
}
FIELD_LABEL_OVERRIDES = {
    "thesis": {"publisher": "University"},
    "report": {"publisher": "Institution"},
}
COMMON_FIELDS = ["year", "doi", "url", "citation_key", "short_title", "language",
                 "keywords", "abstract", "extra"]


def schema_payload() -> dict:
    return {
        "fields": [asdict(f) for f in FIELDS],
        "itemTypes": [{"key": k, "label": v} for k, v in ITEM_TYPES],
        "typeFields": TYPE_FIELDS,
        "containerLabels": CONTAINER_LABELS,
        "fieldLabelOverrides": FIELD_LABEL_OVERRIDES,
        "commonFields": COMMON_FIELDS,
        "rareFields": [f.key for f in FIELDS if f.rare],
    }
