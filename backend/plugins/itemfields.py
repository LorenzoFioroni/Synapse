"""Per-item fields at work: ``plugin.fields`` (what a plugin declares and sets) and ``FieldIndex``
(what the app reads: values for the item list and the item pane, search filters, refreshing stale
values in the background). The values and their rules are in fields.py."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .errors import PluginError
from .fields import BATCH, LATER, LATER_WAIT, FieldDef, FieldStore, declare, key_of, keys_of, matches, pick
from .http import HttpError

log = logging.getLogger(__name__)


class PluginFields:
    """``plugin.fields``."""

    LATER = LATER  # a refresher's answer for a paper it could not ask about: try again later

    def __init__(
        self,
        plugin_id: str,
        plugin_name: str,
        store: Callable[[], FieldStore],
        find_item,
        changed: Callable[[], None] = lambda: None,
    ):
        self._pid = plugin_id
        self._pname = plugin_name
        self._store = store
        self._find = find_item
        self._changed = changed
        self.defs: dict[str, FieldDef] = {}

    def declare(
        self,
        name: str,
        *,
        type: str = "text",  # noqa: A002 - the word plugins write
        label: str | None = None,
        description: str = "",
        source: str = "",
        options: list[str] | None = None,
        refresh: str | float | None = None,
        refresher: Callable | None = None,
    ) -> FieldDef:
        """A field of this plugin: ``type`` is int, float, text, date, bool, url or enum (with
        ``options``). With a ``refresher(papers) -> {key: value}``, Synapse asks it for the values
        of library items that have none yet, and again after ``refresh`` ("7d"), in batches, in the
        web app's background."""
        if name in self.defs:
            raise PluginError(f"Field {name!r} is declared twice")
        d = declare(
            self._pid,
            self._pname,
            name,
            type=type,
            label=label,
            description=description,
            source=source,
            options=options,
            refresh=refresh,
            refresher=refresher,
        )
        self.defs[name] = d
        return d

    def _def(self, name: str) -> FieldDef:
        d = self.defs.get(name)
        if not d:
            raise PluginError(f"No field {name!r}: declare it first with plugin.fields.declare")
        return d

    def key(self, ref) -> str:
        """The key ``ref`` (an item, its id, ``{"doi": …}``, ``{"arxiv": …}``) is stored under."""
        return key_of(ref, self._find)

    def set(self, ref, name: str, value, *, source_url: str | None = None) -> None:
        """Store a value (``None``: looked, nothing found)."""
        d = self._def(name)
        self._store().put(self._pid, name, {self.key(ref): (d.clean(value), source_url)})
        self._changed()

    def set_many(self, name: str, values: dict) -> None:
        """Store ``{ref: value}`` (refs as for ``set``) in one go."""
        d = self._def(name)
        self._store().put(self._pid, name, {self.key(r): (d.clean(v), None) for r, v in values.items()})
        self._changed()

    def get(self, ref, name: str):
        """The stored value for ``ref`` (an item is looked up under all its keys), or None."""
        self._def(name)
        if isinstance(ref, dict) and ref.get("id") and ("title" in ref or "item_type" in ref):
            keys = keys_of(ref)
        else:
            item = self._find(str(ref)) if isinstance(ref, str) and ":" not in ref else None
            keys = keys_of(item) if item else [self.key(ref)]
        row = pick(self._store().rows(self._pid, name), keys)
        return row["value"] if row else None

    def clear(self, name: str | None = None, papers=None) -> int:
        """Forget the stored values of one field (or of all of this plugin's); with ``papers``
        (refs as for ``set``), only theirs: papers that left a list, say."""
        if name:
            self._def(name)
        keys = None if papers is None else [self.key(r) for r in papers]
        n = self._store().delete(self._pid, name, keys)
        if n:
            self._changed()
        return n


class FieldIndex:
    """The fields of the running plugins, as the app uses them."""

    def __init__(self, path, defs: Callable[[], list[FieldDef]], changed: Callable[[], object] | None = None):
        self._path = path
        self._store: FieldStore | None = None
        self._defs = defs
        self._on_change = changed  # tells the open pages (the change feed's "fields")
        # papers a refresher answered LATER for: (plugin, field, key) -> when to hand them over again
        self._later: dict[tuple[str, str, str], float] = {}

    def changed(self) -> None:
        if self._on_change:
            self._on_change()

    @property
    def store(self) -> FieldStore:
        if self._store is None:
            self._store = FieldStore(self._path)
        return self._store

    def defs(self) -> list[FieldDef]:
        return self._defs()

    def describe(self) -> list[dict]:
        return [d.describe() for d in self.defs()]

    # ------------------------------------------------------------------ values
    def values(self, items: list[dict]) -> dict[str, dict[str, object]]:
        """``{item id: {field id: value}}`` for the known (not None) values of these items."""
        defs = self.defs()
        out: dict[str, dict[str, object]] = {}
        if not defs or not items:
            return out
        keys = {p["id"]: keys_of(p) for p in items}
        for d in defs:
            rows = self.store.rows(d.plugin, d.name)
            if not rows:
                continue
            for iid, ks in keys.items():
                row = pick(rows, ks)
                if row and row["value"] is not None:
                    out.setdefault(iid, {})[d.id] = row["value"]
        return out

    def attach(self, items: list[dict]) -> list[dict]:
        """The items with their values as ``_f`` (what the list sends to the browser)."""
        vals = self.values(items)
        return [{**p, "_f": vals[p["id"]]} if p["id"] in vals else p for p in items]

    def for_item(self, item: dict) -> list[dict]:
        """Each field with this item's value, when it was fetched and from where (the item pane)."""
        defs = self.defs()
        if not defs:
            return []
        keys = keys_of(item)
        rows: dict[tuple[str, str], dict[str, dict]] = {}
        for r in self.store.for_keys(keys):
            rows.setdefault((r["plugin"], r["name"]), {})[r["key"]] = r
        out = []
        for d in defs:
            row = pick(rows.get((d.plugin, d.name), {}), keys)
            out.append(
                {
                    **d.describe(),
                    "value": row["value"] if row else None,
                    "checked": bool(row),
                    "updated": row["updated"] if row else None,
                    "source_url": row["source_url"] if row else None,
                }
            )
        return out

    def labelled(self, item: dict) -> dict[str, object]:
        """``{"Citations (Citation Counts)": 152}`` for Claude (known values only)."""
        return {f"{f['label']} ({f['plugin_name']})": f["value"] for f in self.for_item(item) if f["value"] is not None}

    # ------------------------------------------------------------------ search
    def search_filters(self) -> dict[str, Callable[[dict, str], bool]]:
        """Filters for library/search.py: each field by its name and by ``<plugin>.<name>``. A
        name two plugins use, or one the search knows itself, works only in the long form."""
        defs = self.defs()
        if not defs:
            return {}
        counts: dict[str, int] = {}
        for d in defs:
            counts[d.name] = counts.get(d.name, 0) + 1
        out: dict[str, Callable[[dict, str], bool]] = {}
        for d in defs:
            fn = self._filter(d)
            out[d.id.lower()] = fn
            if counts[d.name] == 1:
                out.setdefault(d.name, fn)
        return out

    def _filter(self, d: FieldDef) -> Callable[[dict, str], bool]:
        cache: dict[str, dict] = {}

        def check(p: dict, spec: str) -> bool:
            if "rows" not in cache:
                cache["rows"] = self.store.rows(d.plugin, d.name)
            return matches(d, pick(cache["rows"], keys_of(p)), spec)

        return check

    # ------------------------------------------------------------------ refreshing
    def due(self, d: FieldDef, items: list[dict], now: float | None = None) -> list[dict]:
        """The library items whose value is missing, or older than the field's ``refresh``:
        never looked first, then the oldest."""
        now = time.time() if now is None else now
        rows = self.store.rows(d.plugin, d.name)
        fresh_after = now - d.refresh if d.refresh else None
        missing, stale = [], []
        for p in items:
            ks = keys_of(p)
            if self._later.get((d.plugin, d.name, ks[0]), 0) > now:
                continue
            row = pick(rows, ks)
            if row is None:
                missing.append(p)
            elif fresh_after is not None and row["updated"] < fresh_after:
                stale.append((row["updated"], p))
        return missing + [p for _, p in sorted(stale, key=lambda x: x[0])]

    def refresh(self, d: FieldDef, items: list[dict], plog: logging.Logger, limit: int = BATCH) -> int:
        """Ask the field's refresher for one batch of due items; returns how many it was asked
        about (0: nothing due). Values it does not return are stored as None (looked, nothing
        found), so they are asked about again only after ``refresh``; those it answers ``LATER``
        are left as they were and handed over again in an hour. An unreachable web (``HttpError``)
        leaves the whole batch as it was."""
        if not d.refresher:
            return 0
        batch = self.due(d, items)[:limit]
        if not batch:
            return 0
        papers = []
        for p in batch:
            doi, arx = _ids(p)
            papers.append(
                {
                    "key": keys_of(p)[0],
                    "item_id": p["id"],
                    "doi": doi,
                    "arxiv": arx,
                    "title": p.get("title") or "",
                    "year": p.get("year"),
                }
            )
        try:
            got = d.refresher(papers) or {}
        except HttpError as e:
            plog.warning("Refreshing %s could not finish: %s", d.name, e)
            return 0
        if not isinstance(got, dict):
            raise PluginError(f"The refresher of {d.name} must return a dict {{key: value}}")
        clean: dict[str, tuple[object, str | None]] = {}
        later = 0
        for p in papers:
            k = p["key"]
            v = got.get(k)
            if v is LATER:
                self._later[(d.plugin, d.name, k)] = time.time() + LATER_WAIT
                later += 1
                continue
            if isinstance(v, tuple) and len(v) == 2:  # (value, source_url)
                clean[k] = (d.clean(v[0]), v[1])
            else:
                clean[k] = (d.clean(v), None)
        if clean:
            self.store.put(d.plugin, d.name, clean)
            self.changed()
        plog.info(
            "Refreshed %s for %d items (%d found%s)",
            d.name,
            len(papers),
            sum(v is not None for v, _ in clean.values()),
            f", {later} for later" if later else "",
        )
        return len(papers)


def _ids(p: dict) -> tuple[str | None, str | None]:
    from ..library.identifiers import identifiers

    return identifiers(p)
