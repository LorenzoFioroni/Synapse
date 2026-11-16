"""What the companion downloads from the server: the browser extension folder and the companion
itself, the ``synapse_companion`` package as one runnable zip (``python synapse_companion.pyz``).

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
PACKAGE = APP_ROOT / "synapse_companion"
BUILD_FILE = "build.json"
STAMP = (1980, 1, 1, 0, 0, 0)  # a fixed date keeps the zip, and so its hash, stable


def short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _files(root: Path) -> list[tuple[str, Path]]:
    """Regular files only (no links out of the folder, no hidden files or bytecode), by name."""
    found = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if (
            path.is_symlink()
            or not path.is_file()
            or any(p.startswith(".") or p == "__pycache__" for p in rel.parts)
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
    return build, _zip([*contents, (BUILD_FILE, json.dumps({"build": build}).encode())])


def _zip(contents: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in contents:
            info = zipfile.ZipInfo(name, STAMP)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return out.getvalue()


# The zip's own entry point: runs the package's (synapse_companion/__main__.py).
PYZ_MAIN = b"import runpy\n\nrunpy.run_module('synapse_companion', run_name='__main__', alter_sys=True)\n"


def script(root: Path = PACKAGE) -> tuple[str, bytes]:
    """(build, bytes) of ``synapse_companion.pyz``; its build is the hash of those bytes, which
    the companion compares with its own file."""
    if not (root / "__main__.py").is_file():
        return "", b""
    contents = [("__main__.py", PYZ_MAIN)]
    contents += [(f"synapse_companion/{name}", path.read_bytes()) for name, path in _files(root)]
    data = _zip(contents)
    return short_hash(data), data
