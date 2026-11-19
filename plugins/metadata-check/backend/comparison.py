"""Stored metadata findings and remembered rejections."""

import time

from backend.plugins import scholarly


def _real_doi(value) -> str:
    doi = str(value or "").lower().removeprefix("https://doi.org/")
    return "" if doi.startswith("10.48550/arxiv.") else doi


def is_preprint(item: dict) -> bool:
    """Whether the item is an arXiv preprint (archive_id or preprint item_type)."""
    return bool(scholarly.arxiv_id_of(item))


def find(current: dict, found: dict, is_arxiv: bool) -> dict:
    """Differences between current record and fetched metadata: published version and changed fields."""
    changes = scholarly.compute_changes(current, found)
    active_changes = [c for c in changes if c["checked"]]

    out: dict = {
        "fields": {c["key"]: str(c["fetched"]) for c in active_changes},
        "changes": changes,
        "fetched": found,
    }
    published = _real_doi(found.get("doi"))
    if is_arxiv and published and published != _real_doi(current.get("doi")):
        out["published"] = published
        if "doi" in out["fields"]:
            del out["fields"]["doi"]
    return out


def verdict(
    finding: dict, ignored: dict | None, is_paper_ignored: bool = False, version: dict | None = None
) -> str | tuple[str, str]:
    """The field's text for a finding and a newer arXiv version (``arxiv.newer`` with ``url``),
    leaving out what the user ignored: a published version first, then a new arXiv version."""
    if is_paper_ignored:
        return "Up to date · ignored paper"
    ignored = ignored or {}
    gone = ignored.get("fields") or {}
    published = finding.get("published")
    if published and published != ignored.get("published"):
        return (f"Published version: {published}", f"https://doi.org/{published}")
    if version and version["version"] != ignored.get("version"):
        return (f"New arXiv version: v{version['version']}", version.get("url") or "")
    changed = [n for n, v in finding.get("fields", {}).items() if gone.get(n) != v]
    if changed:
        return f"Review: {', '.join(changed)}"
    hidden = (
        (["published version"] if published else [])
        + ([f"arXiv v{version['version']}"] if version else [])
        + list(finding.get("fields", {}))
    )
    return f"Up to date · ignored {', '.join(hidden)}" if hidden else "Up to date"


def ignore_entry(finding: dict, title: str, before: dict | None = None, version: int | None = None) -> dict:
    """What is remembered when the user ignores a finding and the arXiv version offered with it
    (added to an earlier ignore)."""
    before = before or {}
    fields = {**(before.get("fields") or {}), **finding.get("fields", {})}
    shown = {**(before.get("shown") or {}), **finding.get("fields", {})}
    out = {"title": title, "fields": fields, "shown": shown, "at": time.time()}
    published = finding.get("published") or before.get("published")
    if published:
        out["published"] = published
    if version or before.get("version"):
        out["version"] = version or before["version"]
    return out
