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
    # Online lookups
    "contact_email": "",
}

CHOICES = {
    "default_search_mode": set(SEARCH_MODES),
    "theme": {"system", "light", "dark"},
    "density": {"comfortable", "compact"},
    "export_format": {"bibtex", "ris", "endnote", "endnote_xml", "csljson"},
}


class SettingsError(ValueError):
    pass


class SettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        if self.path.exists():
            try:
                stored = json.loads(self.path.read_text("utf-8"))
                self._data.update({k: v for k, v in stored.items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass

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
            else:
                v = "" if v is None else str(v).strip()
            if k in CHOICES and v not in CHOICES[k]:
                raise SettingsError(f"Invalid value for {k}: {v}")
            if k == "citekey_pattern" and not v:
                raise SettingsError("Citation key pattern cannot be empty")
            clean[k] = v
        with self._lock:
            self._data.update(clean)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), "utf-8")
            tmp.replace(self.path)
            return dict(self._data)

    def reset(self) -> dict:
        with self._lock:
            self._data = dict(DEFAULTS)
        return self.update({})
