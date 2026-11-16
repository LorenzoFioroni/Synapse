"""What the companion downloads from the server: the browser extension folder and its own script.

Each carries a short build hash so the companion (and the extension) can tell, from a cheap
``hello``, whether it is out of date. Nothing is cached: the files are small and a hello is rare. A repository checkout has no ``build.json`` of its own; only
the zip served here gets one.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from ..config import APP_ROOT

EXTENSION_DIR = APP_ROOT / "browser_extension"
SCRIPT = APP_ROOT / "synapse_companion.py"
BUILD_FILE = "build.json"
STAMP = (1980, 1, 1, 0, 0, 0)  # a fixed date keeps the zip, and so its hash, stable


def short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _files(root: Path) -> list[tuple[str, Path]]:
    """Regular files only (no links out of the folder), by name."""
    found = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if (
            path.is_symlink()
            or not path.is_file()
            or any(p.startswith(".") for p in rel.parts)
            or str(rel) == BUILD_FILE
        ):
            continue
        found.append((rel.as_posix(), path))
    return found


def extension(root: Path = EXTENSION_DIR) -> tuple[str, bytes]:
    """(build, zip) of the extension folder, or ("", b"") when there is none."""
    if not root.is_dir():
        return "", b""
    digest = hashlib.sha256()
    contents = []
    for name, path in _files(root):
        data = path.read_bytes()
        digest.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
        contents.append((name, data))
    build = digest.hexdigest()[:16]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in [*contents, (BUILD_FILE, json.dumps({"build": build}).encode())]:
            info = zipfile.ZipInfo(name, STAMP)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return build, out.getvalue()


def script(path: Path = SCRIPT) -> tuple[str, bytes]:
    """(build, text) of the companion script."""
    try:
        data = path.read_bytes()
    except OSError:
        return "", b""
    return short_hash(data), data
