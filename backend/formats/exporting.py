"""Shared reference-output preparation, independent of optional export providers."""

from __future__ import annotations

from .common import split_keywords


def prepare(items: list[dict], ids: list[str] | None = None) -> list[dict]:
    """Copy records, preserve selection order and merge tags into portable keywords."""
    out = [dict(item) for item in items]
    if ids is not None:
        order = {key: n for n, key in enumerate(ids)}
        out = sorted((item for item in out if item["id"] in order), key=lambda item: order[item["id"]])
    else:
        out.sort(key=lambda item: ((item.get("authors") or "").lower(), str(item.get("year") or "")))
    for item in out:
        keywords = split_keywords(item.get("keywords")) + list(item.get("tags") or [])
        item["keywords"] = "; ".join(dict.fromkeys(keywords))
    return out


def options(settings) -> dict:
    return {
        "unicode": settings.get("bibtex_unicode"),
        "abstract": settings.get("export_abstract"),
        "pattern": settings.get("citekey_pattern"),
    }
