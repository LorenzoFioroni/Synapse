"""User preferences, stored as JSON next to the library."""
from __future__ import annotations

import json
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
    "theme": "system",            # system | light | dark
    "density": "comfortable",     # comfortable | compact
    "font_size": 13,
    # Behaviour
    "confirm_trash": False,
    "open_pdf_on_double_click": True,
    # Import / export
    "export_format": "bibtex",
    "bibtex_unicode": True,       # False -> escape non-ASCII as LaTeX commands
    "export_abstract": True,
    "import_skip_duplicates": True,
    "import_tags": "ask",              # keywords in imported files become tags: never | always | ask
    # Folders
    "show_subfolder_items": True,      # a folder also lists the items of its subfolders
    # Online lookups
    "contact_email": "",
    "references_auto": True,           # Retrieve Metadata on existing items also fetches their reference lists
    # What to do with items that were just added (by identifier, PDF, import or the browser
    # button): never | always | ask. "Ask" shows a checkbox where the item is added.
    "add_fetch_metadata": "ask",       # imported items: look up their metadata online (then review it)
    "add_find_pdf": "ask",             # look for an open-access PDF when the new item has none
    "add_fetch_references": "always",  # fetch the list of papers the new item cites
    "openalex_api_key": "",            # optional: OpenAlex allows only ~5 reference lookups a day without one
    "semantic_scholar_api_key": "",    # optional: avoids the shared rate limit
    # Notes (beta): shows the Notes entry in the sidebar
    "notes_enabled": True,
    "notes_autosave_ms": 1500,         # save after this long without typing (also on blur)
    # Deleted notes and attachments no note uses any more are deleted for good after this long
    "notes_trash_retention": "1m",     # never | 1d | 1w | 2w | 1m | 6m
    "notes_width": "readable",         # readable (a column of ~760 px) | full (the whole pane)
    # Display equations: "latex" numbers equation/align… environments (and \tag); "all" numbers every one
    "notes_equation_numbers": "latex",
    # Backups of library.db, settings and notes (not the PDFs): see backend/backup.py
    "backup_interval": "daily",        # off | daily | weekly
    "backup_keep": 14,                 # newest archives kept
    "backup_dir": "",                  # empty: data/backups
}

ASK_CHOICES = ("never", "always", "ask")

CHOICES = {
    "default_search_mode": set(SEARCH_MODES),
    "theme": {"system", "light", "dark"},
    "density": {"comfortable", "compact"},
    "export_format": {"bibtex", "ris", "endnote", "endnote_xml", "csljson"},
    "import_tags": set(ASK_CHOICES),
    "add_fetch_metadata": set(ASK_CHOICES),
    "add_find_pdf": set(ASK_CHOICES),
    "add_fetch_references": set(ASK_CHOICES),
    "notes_trash_retention": {"never", "1d", "1w", "2w", "1m", "6m"},
    "notes_width": {"readable", "full"},
    "notes_equation_numbers": {"latex", "all"},
    "backup_interval": {"off", "daily", "weekly"},
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
    return stored


class SettingsError(ValueError):
    pass


class SettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        if self.path.exists():
            try:
                stored = _migrate(json.loads(self.path.read_text("utf-8")))
                self._data.update({k: v for k, v in stored.items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass

    def reload(self) -> None:
        """Read settings.json again (after a backup was restored)."""
        data = dict(DEFAULTS)
        if self.path.exists():
            try:
                stored = _migrate(json.loads(self.path.read_text("utf-8")))
                data.update({k: v for k, v in stored.items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass
        with self._lock:
            self._data = data

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
                except (TypeError, ValueError):
                    raise SettingsError(f"{k} must be a number")
                if k == "font_size" and not 11 <= v <= 18:
                    raise SettingsError("Font size must be between 11 and 18")
                if k == "notes_autosave_ms" and not 300 <= v <= 60000:
                    raise SettingsError("Autosave delay must be between 0.3 and 60 seconds")
                if k == "backup_keep" and not 1 <= v <= 365:
                    raise SettingsError("Keep between 1 and 365 backups")
            else:
                v = "" if v is None else str(v).strip()
            if k in CHOICES and v not in CHOICES[k]:
                raise SettingsError(f"Invalid value for {k}: {v}")
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
                    raise SettingsError(f"Cannot write to {d}: {e.strerror or e}")
            clean[k] = v
        with self._lock:
            self._data.update(clean)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), "utf-8")
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
