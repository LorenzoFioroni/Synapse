"""User preferences, stored as JSON next to the library."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

SEARCH_MODES = {
    "basic": "Title, Abstract & Authors",
    "all": "All Fields",
    "fulltext": "All Fields & Full Text",
}

DEFAULTS: dict = {
    # Citation keys
    "citekey_pattern": "{auth}{Title}{year}",
    "citekey_auto": True,
    # Search
    "default_search_mode": "basic",
    "fulltext_auto_index": True,
    # Appearance
    "theme": "system",  # system | light | dark
    "density": "comfortable",  # comfortable | compact
    "font_size": 13,
    # Behaviour
    "confirm_trash": False,
    "open_pdf_on_double_click": True,
    # Import / export
    "export_format": "export-formats.bibtex",
    "bibtex_unicode": True,  # False -> escape non-ASCII as LaTeX commands
    "export_abstract": True,
    "import_skip_duplicates": True,
    "import_tags": "ask",  # keywords in imported files become tags: never | always | ask
    # Folders
    "show_subfolder_items": True,  # a folder also lists the items of its subfolders
    # Online lookups
    "contact_email": "",
    "references_auto": True,  # Retrieve Metadata on existing items also fetches their reference lists
    # What to do with items that were just added (by identifier, PDF, import or the browser
    # button): never | always | ask. "Ask" shows a checkbox where the item is added.
    "add_fetch_metadata": "ask",  # imported items: look up their metadata online (then review it)
    "add_find_pdf": "ask",  # look for an open-access PDF when the new item has none
    "add_fetch_references": "always",  # fetch the list of papers the new item cites
    "openalex_api_key": "",  # optional: OpenAlex allows only ~5 reference lookups a day without one
    "semantic_scholar_api_key": "",  # optional: avoids the shared rate limit
    # Notes (beta): shows the Notes entry in the sidebar
    "notes_enabled": True,
    "notes_autosave_ms": 1500,  # save after this long without typing (also on blur)
    # Deleted notes and attachments no note uses any more are deleted for good after this long
    "notes_trash_retention": "1m",  # never | 1d | 1w | 2w | 1m | 6m
    "notes_width": "readable",  # readable (a column of ~760 px) | full (the whole pane)
    # Display equations: "labelled" numbers those with a \label (and equation/align… as in LaTeX);
    # "all" numbers every one
    "notes_equation_numbers": "labelled",
    "notes_math_physics": True,  # the physics package's commands (\vb, \dv, \qty…) in formulas
    "notes_math_macros": "",  # \newcommand definitions used by every note
    # PDF viewer: where PDFs open. "tabs": the app's own tabs, beside the Synapse tab (also in the
    # app window, python server.py --window); "browser": a browser tab (or window) of their own.
    "pdf_open_in": "tabs",
    # PDF viewer: the colour each annotation tool starts with. A tool keeps the colour it was last
    # used with on a device until its default here changes (frontend/src/viewer/annotations/prefs.ts).
    "annotation_color_highlight": "#ffff00",  # also the area highlight
    "annotation_color_underline": "#ff0000",
    "annotation_color_text": "#000000",
    "annotation_color_box": "#000000",  # the border
    "annotation_fill_box": "#ffff00",  # when a box is filled
    "annotation_color_draw": "#000000",
    "annotation_snap_words": True,  # highlights and underlines take whole words
    # Backups of library.db, settings and notes (not the PDFs): see backend/backup.py
    "backup_interval": "daily",  # off | daily | weekly
    "backup_keep": 14,  # newest archives kept
    "backup_dir": "",  # empty: data/backups
}

ASK_CHOICES = ("never", "always", "ask")
COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")

CHOICES = {
    "default_search_mode": set(SEARCH_MODES),
    "theme": {"system", "light", "dark"},
    "density": {"comfortable", "compact"},
    "import_tags": set(ASK_CHOICES),
    "add_fetch_metadata": set(ASK_CHOICES),
    "add_find_pdf": set(ASK_CHOICES),
    "add_fetch_references": set(ASK_CHOICES),
    "notes_trash_retention": {"never", "1d", "1w", "2w", "1m", "6m"},
    "notes_width": {"readable", "full"},
    "notes_equation_numbers": {"labelled", "all"},
    "pdf_open_in": {"tabs", "browser"},
    "backup_interval": {"off", "daily", "weekly"},
}


# The library exports Synapse always had are the plugin Export Formats, whose formats are named
# "<plugin>.<format>" like every plugin's; the names they had before still mean them.
RENAMED_EXPORT_FORMATS = {
    old: f"export-formats.{old}" for old in ("bibtex", "ris", "endnote", "endnote_xml", "csljson")
}


def _migrate(stored: dict) -> dict:
    """Settings files written by older versions."""
    stored = dict(stored)
    # Every key is written back on save, so an old True is just the old default: only a
    # deliberate False is carried over.
    if stored.get("import_keywords_as_tags") is False and "import_tags" not in stored:
        stored["import_tags"] = "never"
    if stored.get("references_auto") is False and "add_fetch_references" not in stored:
        stored["add_fetch_references"] = "never"
    if stored.get("export_format") in RENAMED_EXPORT_FORMATS:
        stored["export_format"] = RENAMED_EXPORT_FORMATS[stored["export_format"]]
    if stored.get("notes_equation_numbers") == "latex":  # value used by an early version of equation numbering
        stored["notes_equation_numbers"] = "labelled"
    return stored


class SettingsError(ValueError):
    pass


class SettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        # settings of features that became core plugins: kept in the file (and readable with
        # ``legacy``) so a plugin can take its values over when it first starts
        self._legacy: dict = {}
        if self.path.exists():
            try:
                stored = _migrate(json.loads(self.path.read_text("utf-8")))
                self._data.update({k: v for k, v in stored.items() if k in DEFAULTS})
                self._legacy = {k: v for k, v in stored.items() if k not in DEFAULTS}
            except (OSError, ValueError):
                pass

    def reload(self) -> None:
        """Read settings.json again (after a backup was restored)."""
        data = dict(DEFAULTS)
        if self.path.exists():
            try:
                stored = _migrate(json.loads(self.path.read_text("utf-8")))
                data.update({k: v for k, v in stored.items() if k in DEFAULTS})
                legacy = {k: v for k, v in stored.items() if k not in DEFAULTS}
            except (OSError, ValueError):
                legacy = {}
        else:
            legacy = {}
        with self._lock:
            self._data = data
            self._legacy = legacy

    def legacy(self, key: str):
        """The stored value of a setting Synapse no longer has (it moved to a core plugin), or None."""
        with self._lock:
            return self._legacy.get(key)

    def all(self) -> dict:
        with self._lock:
            return dict(self._data)

    def get(self, key: str):
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key))

    def update(self, changes: dict) -> dict:
        clean = {}
        for k, v in changes.items():
            if k not in DEFAULTS:
                raise SettingsError(f"Unknown setting: {k}")
            default = DEFAULTS[k]
            if isinstance(default, bool):
                v = bool(v)
            elif isinstance(default, int):
                try:
                    v = int(v)
                except (TypeError, ValueError) as e:
                    raise SettingsError(f"{k} must be a number") from e
                if k == "font_size" and not 11 <= v <= 18:
                    raise SettingsError("Font size must be between 11 and 18")
                if k == "notes_autosave_ms" and not 300 <= v <= 60000:
                    raise SettingsError("Autosave delay must be between 0.3 and 60 seconds")
                if k == "backup_keep" and not 1 <= v <= 365:
                    raise SettingsError("Keep between 1 and 365 backups")
            else:
                v = "" if v is None else str(v).strip()
            if k == "export_format" and not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", v):
                raise SettingsError("Invalid export format id")
            if k in CHOICES and v not in CHOICES[k]:
                raise SettingsError(f"Invalid value for {k}: {v}")
            if k.startswith(("annotation_color_", "annotation_fill_")):
                v = v.lower()
                if not COLOR_RE.match(v):
                    raise SettingsError("A colour looks like #ffcc00")
            if k == "notes_math_macros" and len(v) > 20000:
                raise SettingsError("Math macros: at most 20,000 characters")
            if k == "citekey_pattern" and not v:
                raise SettingsError("Citation key pattern cannot be empty")
            if k == "backup_dir" and v:
                d = Path(v).expanduser()
                if not d.is_absolute():
                    raise SettingsError("The backup folder must be a full path, e.g. /Users/you/Dropbox/Backups")
                try:
                    d.mkdir(parents=True, exist_ok=True)
                    probe = d / ".write-test"
                    probe.write_text("ok")
                    probe.unlink()
                except OSError as e:
                    raise SettingsError(f"Cannot write to {d}: {e.strerror or e}") from e
            clean[k] = v
        with self._lock:
            self._data.update(clean)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({**self._legacy, **self._data}, indent=2, ensure_ascii=False), "utf-8")
            tmp.replace(self.path)
            return dict(self._data)

    def wants(self, key: str, asked: bool | None = None) -> bool:
        """Should an "on add" action run? `asked` is the user's answer where the setting is "ask"
        (None when nobody could be asked: then only "always" runs)."""
        v = self.get(key)
        return v == "always" or (v == "ask" and bool(asked))

    def reset(self) -> dict:
        with self._lock:
            self._data = dict(DEFAULTS)
        return self.update({})
