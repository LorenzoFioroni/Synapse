"""Per-item fields: typed values that plugins attach to papers (citation counts, a score, a
status). Synapse stores and indexes them in ``data/fields.db``, keyed by plugin, field and the
paper's identity: its DOI or arXiv id, so a value also belongs to a paper not (yet) in the library
and stays with it when "+" adds it, else the library item's id. One declaration makes a field
show up as a list column, a search filter (``citations:>100``), a row in the item pane and data
Claude sees.

A stored value may be ``None``: the plugin looked and found nothing ("unknown"), which is not the
same as never having looked (no row) and is kept apart from 0.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..library.identifiers import identifiers, norm_arxiv, norm_doi
from ..library.search import normalize
from .errors import PluginError
from .schedule import every_seconds

TYPES = ("int", "float", "text", "date", "bool", "url", "enum")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
BATCH = 50  # papers handed to a refresher at a time
LATER_WAIT = 3600  # seconds before a paper a refresher could not ask about (LATER) is handed over again


class _Later:
    """What a refresher returns for a paper it could not ask about (one of its services was down):
    nothing is stored, and the paper is handed over again in an hour, not at once."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "plugin.fields.LATER"


LATER = _Later()
SCHEMA = """
CREATE TABLE IF NOT EXISTS field_values (
    plugin TEXT NOT NULL,
    name TEXT NOT NULL,
    key TEXT NOT NULL,          -- doi:<doi> | arxiv:<id> | item:<id>
    value TEXT,                 -- JSON; NULL: looked, nothing found
    updated REAL NOT NULL,      -- seconds since the epoch
    source_url TEXT,
    PRIMARY KEY (plugin, name, key)
);
CREATE INDEX IF NOT EXISTS field_values_key ON field_values (key);
"""


# ------------------------------------------------------------------ identities
def keys_of(item: dict) -> list[str]:
    """Every key a library item's values may be stored under, the main one first."""
    doi, arx = identifiers(item)
    out = []
    if doi and not doi.startswith("10.48550/"):
        out.append(f"doi:{doi}")
    if arx:
        out.append(f"arxiv:{arx}")
    if item.get("id"):
        out.append(f"item:{item['id']}")
    return out


def key_of(ref, find_item: Callable[[str], dict | None] | None = None) -> str:
    """The key a plugin's ``ref`` names: a library item (its dict or its id), ``{"doi": …}`` or
    ``{"arxiv": …}``, a paper handed to a refresher (it has a ``"key"``), or a key itself
    (``"doi:10.1103/…"``, ``"arxiv:2610.01234"``)."""
    if isinstance(ref, dict):
        if ref.get("key"):
            return key_of(str(ref["key"]))
        if ref.get("id") and ("title" in ref or "item_type" in ref):
            keys = keys_of(ref)
            if keys:
                return keys[0]
        if norm_doi(ref.get("doi")) and not norm_doi(ref.get("doi")).startswith("10.48550/"):  # type: ignore[union-attr]
            return f"doi:{norm_doi(ref.get('doi'))}"
        if norm_arxiv(ref.get("arxiv")):
            return f"arxiv:{norm_arxiv(ref.get('arxiv'))}"
        if ref.get("item_id") or ref.get("id"):
            return key_of(str(ref.get("item_id") or ref.get("id")), find_item)
        raise PluginError("A paper is named by its item, its id, {'doi': …} or {'arxiv': …}")
    s = str(ref or "").strip()
    kind, _, rest = s.partition(":")
    if kind == "doi" and norm_doi(rest):
        return f"doi:{norm_doi(rest)}"
    if kind == "arxiv" and norm_arxiv(rest):
        return f"arxiv:{norm_arxiv(rest)}"
    if kind == "item" and rest:
        return f"item:{rest}"
    if s and find_item:
        item = find_item(s)
        if item:
            return keys_of(item)[0]
    raise PluginError(f"No paper {s!r}: give an item id, a dict with doi/arxiv, or a key like doi:10.…")


# ------------------------------------------------------------------ declarations
@dataclass
class FieldDef:
    plugin: str
    plugin_name: str
    name: str
    type: str
    label: str
    description: str = ""
    source: str = ""  # where the values come from, shown in the item pane ("OpenAlex")
    options: list[str] = field(default_factory=list)  # enum
    refresh: float | None = None  # seconds after which a value is fetched again
    refresher: Callable | None = None

    @property
    def id(self) -> str:
        return f"{self.plugin}.{self.name}"

    def describe(self) -> dict:
        out: dict[str, object] = {
            "id": self.id,
            "plugin": self.plugin,
            "plugin_name": self.plugin_name,
            "name": self.name,
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "source": self.source,
        }
        if self.options:
            out["options"] = self.options
        return out

    def clean(self, value):
        """``value`` as stored, or PluginError when it does not fit the type (None stays None)."""
        if value is None:
            return None
        t = self.type
        try:
            if t == "int":
                if isinstance(value, bool) or (isinstance(value, float) and value != int(value)):
                    raise ValueError
                return int(value)
            if t == "float":
                if isinstance(value, bool):
                    raise ValueError
                return float(value)
            if t == "bool":
                if not isinstance(value, bool):
                    raise ValueError
                return value
            if t == "date":
                if isinstance(value, datetime):
                    return value.date().isoformat()
                if isinstance(value, date):
                    return value.isoformat()
                return date.fromisoformat(str(value)[:10]).isoformat()
            v = str(value)
            if t == "url" and not re.match(r"^https?://", v):
                raise ValueError
            if t == "enum" and v not in self.options:
                raise ValueError
            return v[:2000]
        except (TypeError, ValueError) as e:
            what = f"one of {', '.join(self.options)}" if t == "enum" else f"a {t}"
            raise PluginError(f"Field {self.name}: {value!r} is not {what}") from e


def declare(plugin_id: str, plugin_name: str, name: str, **kw) -> FieldDef:
    if not NAME_RE.match(name or ""):
        raise PluginError(f"Field name {name!r}: lowercase letters, digits and _")
    t = kw.get("type", "text")
    if t not in TYPES:
        raise PluginError(f"Field {name}: type is one of {', '.join(TYPES)}")
    options = [str(o) for o in kw.get("options") or []]
    if t == "enum" and not options:
        raise PluginError(f"Field {name}: an enum needs options")
    refresh = kw.get("refresh")
    refresher = kw.get("refresher")
    if refresher is not None and not callable(refresher):
        raise PluginError(f"Field {name}: refresher must be a function")
    return FieldDef(
        plugin=plugin_id,
        plugin_name=plugin_name,
        name=name,
        type=t,
        label=str(kw.get("label") or name.replace("_", " ").capitalize())[:60],
        description=str(kw.get("description") or "")[:300],
        source=str(kw.get("source") or "")[:60],
        options=options,
        refresh=every_seconds(refresh) if refresh is not None else None,
        refresher=refresher,
    )


# ------------------------------------------------------------------ the store
class FieldStore:
    """``data/fields.db`` (in backups)."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def put(self, plugin: str, name: str, values: dict[str, tuple[object, str | None]]) -> None:
        """Store ``{key: (value, source_url)}`` (values already cleaned)."""
        now = time.time()
        rows = [(plugin, name, k, None if v is None else json.dumps(v), now, url) for k, (v, url) in values.items()]
        with self._lock, self._connect() as c:
            c.executemany(
                "INSERT INTO field_values (plugin, name, key, value, updated, source_url) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (plugin, name, key) DO UPDATE SET value = excluded.value, updated = excluded.updated, "
                "source_url = excluded.source_url",
                rows,
            )

    def delete(self, plugin: str, name: str | None = None, keys: Iterable[str] | None = None) -> int:
        sql, args = "DELETE FROM field_values WHERE plugin = ?", [plugin]
        if name:
            sql, args = sql + " AND name = ?", [*args, name]
        with self._lock, self._connect() as c:
            if keys is None:
                return c.execute(sql, args).rowcount
            return sum(c.execute(sql + " AND key = ?", [*args, k]).rowcount for k in keys)

    def expire(self, plugin: str, name: str, keys: Iterable[str] | None = None) -> int:
        """Make stored values look as old as can be, so they are refreshed first next time; the
        values themselves stay until the new ones arrive."""
        sql, args = "UPDATE field_values SET updated = 0 WHERE plugin = ? AND name = ?", [plugin, name]
        with self._lock, self._connect() as c:
            if keys is None:
                return c.execute(sql, args).rowcount
            return sum(c.execute(sql + " AND key = ?", [*args, k]).rowcount for k in keys)

    def rows(self, plugin: str, name: str) -> dict[str, dict]:
        """Every stored value of one field: ``{key: {value, updated, source_url}}``."""
        with self._connect() as c:
            cur = c.execute(
                "SELECT key, value, updated, source_url FROM field_values WHERE plugin = ? AND name = ?", (plugin, name)
            )
            return {
                r["key"]: {
                    "value": None if r["value"] is None else json.loads(r["value"]),
                    "updated": r["updated"],
                    "source_url": r["source_url"],
                }
                for r in cur
            }

    def for_keys(self, keys: list[str]) -> list[dict]:
        if not keys:
            return []
        with self._connect() as c:
            cur = c.execute(
                f"SELECT plugin, name, key, value, updated, source_url FROM field_values WHERE key IN ({','.join('?' * len(keys))})",
                keys,
            )
            return [
                {**dict(r), "value": None if r["value"] is None else json.loads(r["value"])} for r in cur.fetchall()
            ]


def pick(rows: dict[str, dict], keys: list[str]) -> dict | None:
    """Of the rows stored under an item's keys, the newest."""
    found = [rows[k] for k in keys if k in rows]
    return max(found, key=lambda r: r["updated"]) if found else None


# ------------------------------------------------------------------ search filters
_NUM = re.compile(r"^(>=|<=|>|<|=)?\s*(-?\d+(?:\.\d+)?)$")
_RANGE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(?:-|\.\.)\s*(-?\d+(?:\.\d+)?)$")
_TRUE = {"true", "yes", "1", "on"}
_FALSE = {"false", "no", "0", "off"}


def matches(f: FieldDef, row: dict | None, spec: str) -> bool:
    """Whether a value (``row``; None when never looked) matches a filter like ``>100``, ``10-20``,
    ``none`` (no known value), ``any`` (a known value), a word, or ``true``/``false``."""
    s = spec.strip()
    v = row["value"] if row else None
    low = s.lower()
    if low in ("none", "unknown", "missing"):
        return v is None
    if low in ("any", "some", "set"):
        return v is not None
    if v is None:
        return False
    if f.type in ("int", "float"):
        if m := _RANGE.match(s):
            return float(m.group(1)) <= float(v) <= float(m.group(2))
        if m := _NUM.match(s):
            op, n = m.group(1) or "=", float(m.group(2))
            return {"=": v == n, ">": v > n, "<": v < n, ">=": v >= n, "<=": v <= n}[op]
        return False
    if f.type == "bool":
        return (low in _TRUE and v is True) or (low in _FALSE and v is False)
    if f.type == "date":
        for op in (">=", "<=", ">", "<"):
            if s.startswith(op):
                d = s[len(op) :].strip()
                return {">": v > d, "<": v < d, ">=": v >= d, "<=": v <= d}[op]
        return str(v).startswith(s)
    return normalize(s) in normalize(v)
