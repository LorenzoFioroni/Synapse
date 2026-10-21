"""Colours given to notes folders (default: none). A note takes the colour of the nearest coloured
folder above it; the app shows it on the folder's icon, the note's tab and the window's title bar.

Kept in ``<vault>/.folders.json`` (hidden like ``.trash`` and ``.history``, so it goes into
backups with the notes): ``{"folder/path": "#rrggbb"}``. Renaming or moving a folder in the app
moves its colour and those of the folders inside (``moved``); a folder renamed outside the app
loses its colour, and a deleted one keeps it, so it comes back if the folder is restored.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

FILE = ".folders.json"
COLOR = re.compile(r"^#[0-9a-f]{6}$")


class FolderColorError(ValueError):
    pass


class FolderColors:
    def __init__(self, vault_root: Path | str):
        self.path = Path(vault_root) / FILE
        self._lock = threading.Lock()

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and COLOR.match(v)}

    def _write(self, data: dict[str, str]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(sorted(data.items())), indent=2, ensure_ascii=False), "utf-8")
        tmp.replace(self.path)

    def all(self) -> dict[str, str]:
        with self._lock:
            return self._read()

    def set(self, folder: str, color: str | None) -> dict[str, str]:
        """Give ``folder`` a colour (``#rrggbb``), or none (None or empty)."""
        folder = folder.strip("/")
        if not folder:
            raise FolderColorError("Choose a folder")
        color = (color or "").strip().lower() or None
        if color is not None and not COLOR.match(color):
            raise FolderColorError("A colour looks like #0090ff")
        with self._lock:
            data = self._read()
            if color:
                data[folder] = color
            else:
                data.pop(folder, None)
            self._write(data)
            return data

    def moved(self, src: str, dst: str) -> None:
        """A folder moved from ``src`` to ``dst``: its colour and its subfolders' go with it."""
        src, dst = src.strip("/"), dst.strip("/")
        if not src or src == dst:
            return
        with self._lock:
            data = self._read()
            out = {}
            changed = False
            for k, v in data.items():
                if k == src or k.startswith(src + "/"):
                    out[dst + k[len(src) :]] = v
                    changed = True
                else:
                    out.setdefault(k, v)
            if changed:
                self._write(out)
