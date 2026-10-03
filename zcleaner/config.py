"""Paths and user settings (persisted as JSON in the workspace folder)."""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
WORK_DIR = Path(os.environ.get("ZCLEANER_HOME", APP_DIR / "workspace"))
BACKUP_DIR = WORK_DIR / "backups"
SETTINGS_FILE = WORK_DIR / "settings.json"
SECRETS_FILE = WORK_DIR / "secrets.json"
DB_FILE = WORK_DIR / "workspace.sqlite3"

# Fields (by *base* field name) that are stored as LaTeX.
LATEX_FIELDS_DEFAULT = [
    "title", "shortTitle", "publicationTitle", "journalAbbreviation", "publisher",
    "place", "series", "seriesTitle", "conferenceName", "edition", "section",
    "meetingName", "university", "institution", "creators",
]

RULES = {
    # id: (label, default enabled, description)
    "whitespace": ("Whitespace", True, "Trim and collapse spaces, tabs and line breaks."),
    "markup": ("HTML/JATS → LaTeX", True, "Convert <i>, <sub>, <sup>, entities etc. to LaTeX."),
    "latex": ("Unicode → LaTeX", True, "Encode special characters (accents, Greek, symbols, dashes) as LaTeX."),
    "latex_escape": ("Escape & % # _", True, "Escape LaTeX special characters in LaTeX fields."),
    "doi": ("DOI clean-up", True, "Strip resolver prefixes/URLs from DOIs; move DOIs from URL/Extra into the DOI field."),
    "title_caps": ("ALL-CAPS titles", True, "Convert shouting titles to sentence case (acronyms preserved)."),
    "title_case": ("Title case policy", True, "Apply the configured title-case policy (sentence/title case)."),
    "recompute": ("Recomputed", True, "Value recomputed from the field as it was before this tool's automatic changes (after a rule improved)."),
    "title_period": ("Trailing period", True, "Remove a trailing period from titles."),
    "creators": ("Creator names", True, "Split 'Last, First' single-field names, fix ALL-CAPS names, empty/duplicate creators."),
    "initials": ("Initials", True, "Normalise initials: 'J.R.' → 'J. R.'."),
    "pages": ("Page ranges", True, "Normalise page ranges (e.g. 'pp. 12 - 19' → '12--19')."),
    "date": ("Dates", True, "Store unambiguous dates in ISO form (YYYY-MM-DD)."),
    "issn_isbn": ("ISSN / ISBN", True, "Normalise ISSN/ISBN formatting."),
    "url": ("URLs", True, "Remove tracking parameters and library-proxy host names."),
    "language": ("Language", True, "Normalise language to ISO codes (English → en)."),
    "journal_names": ("Journal name variants", True, "Unify spellings of the same journal across the library."),
    "journal_abbrev": ("Journal abbreviations", True, "Fill missing journal abbreviations from other items of the same journal."),
    "item_type": ("Item type", True, "Suggest better item types (webpage with DOI, arXiv as preprint, ...)."),
    "tags": ("Tag variants", True, "Merge tags that only differ in case/spacing."),
    "extra": ("Extra field", True, "Tidy the Extra field (blank lines, duplicated lines)."),
}

DEFAULTS = {
    "zotero_api": "http://127.0.0.1:23119/api",
    "library": "users/0",
    "data_dir": "",
    "mailto": "",
    "latex": {
        "enabled": True,
        "style": "braced",           # {\"o}  ('arg' -> \"{o})
        "escape_specials": True,
        "fields": LATEX_FIELDS_DEFAULT,
        "include_abstract": False,
    },
    "title_case": "keep",            # keep | sentence | title
    "acronyms": [],
    "pages_separator": "--",
    "proxy_suffixes": [],
    "rules": {k: v[1] for k, v in RULES.items()},
    "pdf": {
        "types_optional_pdf": ["book"],   # a missing PDF is only 'info' for these
        "types_needing_pdf": ["journalArticle", "conferencePaper", "preprint", "book", "bookSection",
                              "thesis", "report", "manuscript"],
        "filename_template": "{firstCreator} - {year} - {title}",
        "ascii_filenames": True,
        "max_title_words": 12,
        "max_length": 120,
        "rename": True,
        "rename_linked": True,
        "verify_content": True,
        "find_unpaywall": True,
        "find_openalex": True,
        "find_semanticscholar": True,
        "find_arxiv": True,
        "find_europepmc": True,
        "find_oapen": True,
        "find_openlibrary": True,
        "institutional": False,
        "use_browser": True,
        "browser_headless": False,
        "browser_wait_seconds": 30,
        "captcha_wait_seconds": 180,
        "downloads_dir": "",
        "elsevier_api_key": "",
        "doi_resolver": "https://doi.org/",
        "max_download_mb": 150,
    },
    "tags": {"needs_pdf": "_needs-pdf", "pdf_problem": "_pdf-problem", "duplicate": "_duplicate"},
    "lookups": {"crossref": True, "arxiv": True, "doi_content_negotiation": True,
                "search_missing_doi": True, "requests_per_second": 5, "min_match_score": 93},
    "duplicates": {"threshold": 90},
    "commit": {"batch_size": 50, "db_backup": True},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    _lock = threading.Lock()

    def __init__(self):
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        self.data = _merge(DEFAULTS, self._read())

    @staticmethod
    def _read() -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            return {}

    def get(self, *path, default=None):
        cur = self.data
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur

    def update(self, new: dict):
        with self._lock:
            self.data = _merge(self.data, new)
            SETTINGS_FILE.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))

    def rule(self, rid: str) -> bool:
        return bool(self.data.get("rules", {}).get(rid, RULES.get(rid, (0, True))[1]))


class Secrets:
    """Stores the Zotero write key (file readable only by the user)."""

    def __init__(self):
        self.data = {}
        try:
            self.data = json.loads(SECRETS_FILE.read_text())
        except Exception:
            pass

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        if v is None:
            self.data.pop(k, None)
        else:
            self.data[k] = v
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SECRETS_FILE.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, SECRETS_FILE)
        os.chmod(SECRETS_FILE, 0o600)
