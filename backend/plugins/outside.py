"""Papers outside the library, as plugins hand them over (an arXiv listing, search results) and as
the item list shows them: one shape for all, close to a library item's so the list, the search
and the columns work on them unchanged. A record is named by its DOI or arXiv id; its ``id`` in
the list is ``x:<key>`` so it never clashes with a library item's."""

from __future__ import annotations

from .errors import PluginError
from .fields import key_of

TEXT = ("title", "abstract", "journal", "date", "volume", "pages", "url", "publisher")
OUTSIDE_PREFIX = "x:"


def _authors(v) -> str:
    """Authors as "Family, Given; Family, Given", from that string or a list of names ("Given Family" or
    "Family, Given")."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    out = []
    for n in v:
        n = str(n).strip()
        if not n:
            continue
        if "," not in n and " " in n:
            parts = n.split()
            n = f"{parts[-1]}, {' '.join(parts[:-1])}"
        out.append(n)
    return "; ".join(out)


def reasons(v) -> list[dict]:
    """Why a paper is listed, as ``[{"text", "kind", "items"}]``: from lines of text or from the
    reasons ``plugin.services.connections`` gives (``items``: the library papers involved)."""
    out = []
    for r in list(v or [])[:10]:
        if isinstance(r, dict) and r.get("text"):
            items = [str(i) for i in (r.get("items") or [])][:20]
            out.append({"text": str(r["text"])[:300], "kind": str(r.get("kind") or ""), "items": items})
        elif r and not isinstance(r, dict):
            out.append({"text": str(r)[:300], "kind": "", "items": []})
    return out


def record(d: dict, source: str | None = None) -> dict:
    """A plugin's dict as an outside record. It needs a DOI or an arXiv id (``doi``, ``arxiv``);
    besides ``title``, ``authors`` (string or list), ``year``, ``abstract``, ``journal``, ``date``,
    ``categories`` (list), ``item_type``, ``url``, it may carry ``reasons`` (why it is listed:
    lines of text, or the reasons of ``plugin.services.connections``), ``highlight`` (a title badge
    with ``label`` and tooltip ``text``), and ``extra`` (a dict the plugin gets back)."""
    if not isinstance(d, dict):
        raise PluginError("A paper is a dict with at least a doi or an arxiv id")
    try:
        key = key_of({"doi": d.get("doi"), "arxiv": d.get("arxiv") or d.get("arxiv_id")})
    except PluginError as e:
        raise PluginError(f"A paper needs a doi or an arxiv id: {str(d.get('title') or d)[:80]}") from e
    out: dict = {"id": OUTSIDE_PREFIX + key, "key": key, "_outside": True}
    if source:
        out["_source"] = source
    for k in TEXT:
        if d.get(k):
            out[k] = str(d[k])
    out["authors"] = _authors(d.get("authors"))
    if d.get("year"):
        try:
            out["year"] = int(str(d["year"])[:4])
        except ValueError:
            pass
    if key.startswith("doi:"):
        out["doi"] = key[4:]
        out.setdefault("url", f"https://doi.org/{key[4:]}")
    arx = key[6:] if key.startswith("arxiv:") else None
    if not arx and (d.get("arxiv") or d.get("arxiv_id")):
        arx = key_of({"arxiv": d.get("arxiv") or d.get("arxiv_id")})[6:]
    if arx:
        out["arxiv"] = arx
        out["archive_id"] = f"arXiv:{arx}"
        out.setdefault("url", f"https://arxiv.org/abs/{arx}")
    out["item_type"] = str(d.get("item_type") or ("preprint" if arx and not out.get("journal") else "journalArticle"))
    if d.get("categories"):
        cats = d["categories"]
        out["categories"] = ", ".join(cats) if isinstance(cats, list | tuple) else str(cats)
    if d.get("reasons"):
        out["reasons"] = reasons(d["reasons"])
    highlight = d.get("highlight")
    if isinstance(highlight, dict) and highlight.get("label"):
        out["highlight"] = {
            "label": str(highlight["label"])[:40],
            "text": str(highlight.get("text") or highlight["label"])[:300],
        }
    if isinstance(d.get("extra"), dict):
        out["extra_data"] = d["extra"]
    return out
