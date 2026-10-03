"""Get PDFs with *your own* browser — for sites whose captcha rejects any
automated browser (it keeps re-asking after you solve it).

The tool opens the articles in your normal browser (where you are a regular,
already-verified visitor), you click "Download PDF" as usual, and the tool
picks the files up from your Downloads folder: each new PDF is matched to the
right item by its DOI / title, then proposed for review like any other find.
Your downloaded files are left where they are (a copy is attached).
"""
from __future__ import annotations

import shutil
import time
import webbrowser
from pathlib import Path

PARTIAL = (".crdownload", ".download", ".part", ".partial", ".tmp")


def downloads_dir(settings) -> Path:
    d = (settings.get("pdf") or {}).get("downloads_dir") or ""
    return Path(d).expanduser() if d else Path.home() / "Downloads"


def open_in_browser(urls: list[str], pause: float = 1.0):
    for u in urls:
        webbrowser.open_new_tab(u)
        time.sleep(pause)


class DownloadWatcher:
    """New, completely written PDFs in a folder since a moment in time."""

    def __init__(self, folder: Path, since: float):
        self.folder = folder
        self.since = since
        self.seen: set[Path] = set()
        self.sizes: dict[Path, int] = {}

    def poll(self) -> list[Path]:
        ready = []
        if not self.folder.exists():
            return ready
        for p in self.folder.iterdir():
            if p in self.seen or not p.is_file() or p.suffix.lower() != ".pdf" or p.name.startswith("."):
                continue
            if any(p.name.endswith(x) for x in PARTIAL):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime < self.since:
                continue
            # wait until the browser finished writing: size stable, or untouched for a few seconds
            if st.st_size > 0 and (self.sizes.get(p) == st.st_size or st.st_mtime < time.time() - 5):
                self.seen.add(p)
                ready.append(p)
            else:
                self.sizes[p] = st.st_size
        return ready


def stage_copy(src: Path, staging: Path, key: str) -> Path:
    dest = staging / key / ("own-" + src.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest
