"""Safe access to the notes folder.

Every path coming from the outside is a *relative* path like ``papers/smith2021.md``. It is
checked before use so it can never point outside the vault or into the hidden ``.trash``.

Writes are atomic (temp file + rename) and use optimistic concurrency: the caller passes
the hash of the version it loaded, and the write is refused if the file changed since.
Nothing is ever hard-deleted; "delete" moves the file into ``.trash/<timestamp>/``.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

TRASH = ".trash"
ATTACHMENTS = "attachments"
NOTE_SUFFIX = ".md"
_BAD_CHARS = re.compile(r'[\x00-\x1f<>:"\\|?*]')


class VaultError(Exception):
    status = 400


class InvalidPath(VaultError):
    pass


class NoteMissing(VaultError):
    status = 404


class NoteExists(VaultError):
    status = 409


class Conflict(VaultError):
    """The file changed on disk since the caller loaded it."""
    status = 409

    def __init__(self, path: str, current_hash: str | None):
        super().__init__(f"{path} was changed elsewhere")
        self.path = path
        self.current_hash = current_hash


def content_hash(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def slugify(name: str, fallback: str = "untitled") -> str:
    """A file-name-safe version of a title (keeps Unicode letters, case and spaces)."""
    s = unicodedata.normalize("NFC", name or "").strip()
    s = _BAD_CHARS.sub("", s).replace("/", "-")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:120] or fallback


class Vault:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ paths
    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ATTACHMENTS).mkdir(exist_ok=True)

    def clean_rel(self, rel: str, *, suffix: str | None = NOTE_SUFFIX) -> str:
        """Validate and normalise a relative path; raise InvalidPath if it is unsafe."""
        if not isinstance(rel, str) or not rel.strip():
            raise InvalidPath("Empty path")
        rel = unicodedata.normalize("NFC", rel.strip().replace("\\", "/"))
        if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
            raise InvalidPath("Absolute paths are not allowed")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts:
            raise InvalidPath("Empty path")
        for p in parts:
            if p == ".." or p.startswith("."):
                raise InvalidPath(f"Invalid path segment: {p!r}")
            if _BAD_CHARS.search(p):
                raise InvalidPath(f"Invalid characters in {p!r}")
        rel = "/".join(parts)
        if suffix and not rel.lower().endswith(suffix):
            raise InvalidPath(f"Notes must end in {suffix}")
        full = (self.root / rel).resolve()
        if full != self.root and self.root not in full.parents:
            raise InvalidPath("Path escapes the notes folder")
        return rel

    def abs(self, rel: str, *, suffix: str | None = NOTE_SUFFIX) -> Path:
        return self.root / self.clean_rel(rel, suffix=suffix)

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    # ------------------------------------------------------------------ reads
    def exists(self, rel: str) -> bool:
        return self.abs(rel).is_file()

    def read(self, rel: str) -> tuple[str, str]:
        """Return ``(text, hash)``."""
        p = self.abs(rel)
        if not p.is_file():
            raise NoteMissing(rel)
        data = p.read_bytes()
        return data.decode("utf-8", errors="replace"), content_hash(data)

    def current_hash(self, rel: str) -> str | None:
        p = self.abs(rel)
        return content_hash(p.read_bytes()) if p.is_file() else None

    def iter_notes(self):
        """Yield ``(rel, stat)`` for every note, skipping hidden folders and attachments."""
        for dirpath, dirnames, filenames in os.walk(self.root):
            here = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")
                                 and not (here == self.root and d == ATTACHMENTS))
            for f in sorted(filenames):
                if f.startswith(".") or not f.lower().endswith(NOTE_SUFFIX):
                    continue
                p = here / f
                if p.is_symlink():
                    continue
                yield p.relative_to(self.root).as_posix(), p.stat()

    def folders(self) -> list[str]:
        out = []
        for dirpath, dirnames, _ in os.walk(self.root):
            here = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")
                                 and not (here == self.root and d == ATTACHMENTS))
            if here != self.root:
                out.append(here.relative_to(self.root).as_posix())
        return out

    # ----------------------------------------------------------------- writes
    def _atomic_write(self, p: Path, data: bytes) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                tmp.unlink()

    def write(self, rel: str, text: str, *, expected_hash: str | None) -> str:
        """Write a note. ``expected_hash=None`` means "create; must not exist yet".

        Returns the new hash. Raises Conflict when the file on disk is not the version the
        caller loaded, NoteExists when creating over an existing file.
        """
        rel = self.clean_rel(rel)
        p = self.root / rel
        data = text.encode("utf-8")
        with self._lock:
            current = content_hash(p.read_bytes()) if p.is_file() else None
            if expected_hash is None:
                if current is not None:
                    raise NoteExists(rel)
            elif current != expected_hash:
                raise Conflict(rel, current)
            self._atomic_write(p, data)
        return content_hash(data)

    def write_bytes(self, rel: str, data: bytes) -> str:
        """Write a new attachment (never overwrites). Returns the final relative path."""
        rel = self.clean_rel(rel, suffix=None)
        with self._lock:
            p = self.root / rel
            stem, ext, n = p.stem, p.suffix, 1
            while p.exists():
                n += 1
                p = p.with_name(f"{stem}-{n}{ext}")
            self._atomic_write(p, data)
            return self.rel(p)

    def move(self, src: str, dst: str, *, expected_hash: str | None = None) -> str:
        src, dst = self.clean_rel(src), self.clean_rel(dst)
        with self._lock:
            ps, pd = self.root / src, self.root / dst
            if not ps.is_file():
                raise NoteMissing(src)
            if expected_hash is not None and content_hash(ps.read_bytes()) != expected_hash:
                raise Conflict(src, content_hash(ps.read_bytes()))
            if src == dst:
                return dst
            # A case-only rename on a case-insensitive disk (macOS) "exists" already.
            if pd.exists() and not (src.lower() == dst.lower() and ps.samefile(pd)):
                raise NoteExists(dst)
            pd.parent.mkdir(parents=True, exist_ok=True)
            os.replace(ps, pd)
        return dst

    def make_folder(self, rel: str) -> str:
        rel = self.clean_rel(rel, suffix=None)
        if rel.split("/")[0] == ATTACHMENTS:
            raise InvalidPath("The attachments folder is reserved for images")
        (self.root / rel).mkdir(parents=True, exist_ok=True)
        return rel

    def _folder(self, rel: str) -> str:
        """A user folder of notes (never the root, the images folder or a hidden folder)."""
        rel = self.clean_rel(rel, suffix=None)
        if rel.split("/")[0] == ATTACHMENTS:
            raise InvalidPath("The attachments folder holds the images of your notes")
        if not (self.root / rel).is_dir():
            raise NoteMissing(rel)
        return rel

    def notes_in(self, folder: str) -> list[str]:
        """Every note inside a folder (recursively), as vault-relative paths."""
        base = self.root / self._folder(folder)
        out = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for f in filenames:
                if not f.startswith(".") and f.lower().endswith(NOTE_SUFFIX):
                    out.append((Path(dirpath) / f).relative_to(self.root).as_posix())
        return sorted(out)

    def move_folder(self, src: str, dst: str) -> list[tuple[str, str]]:
        """Rename or move a folder with everything in it. Returns [(old note path, new note path)]."""
        src = self._folder(src)
        dst = self.clean_rel(dst, suffix=None)
        if dst.split("/")[0] == ATTACHMENTS:
            raise InvalidPath("The attachments folder is reserved for images")
        if dst == src:
            return []
        if dst.startswith(src + "/"):
            raise InvalidPath("A folder cannot be moved into itself")
        with self._lock:
            ps, pd = self.root / src, self.root / dst
            if pd.exists() and not (src.lower() == dst.lower() and ps.samefile(pd)):
                raise NoteExists(dst)
            notes = self.notes_in(src)
            pd.parent.mkdir(parents=True, exist_ok=True)
            os.replace(ps, pd)
        return [(n, dst + n[len(src):]) for n in notes]

    # ------------------------------------------------------------------ trash
    def trash(self, rel: str) -> str:
        """Move a note to ``.trash/<timestamp>/<rel>``; returns the trash id (that folder name)."""
        rel = self.clean_rel(rel)
        with self._lock:
            p = self.root / rel
            if not p.is_file():
                raise NoteMissing(rel)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            dest = self.root / TRASH / stamp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(p, dest)
        return stamp

    def trash_batch(self, notes: list[str], folders: list[str]) -> tuple[str, list[str]]:
        """Move several notes and folders into one ``.trash/<timestamp>/`` (one undo restores
        them all). Returns the trash id and the note paths that went with them."""
        folders = sorted({self._folder(f) for f in folders})
        folders = [f for f in folders if not any(f.startswith(o + "/") for o in folders)]  # parents cover children
        inside = lambda rel: any(rel.startswith(f + "/") for f in folders)  # noqa: E731
        notes = sorted({n for n in (self.clean_rel(x) for x in notes) if not inside(n)})
        if not notes and not folders:
            raise InvalidPath("Nothing to delete")
        with self._lock:
            for n in notes:
                if not (self.root / n).is_file():
                    raise NoteMissing(n)
            moved = [n for f in folders for n in self.notes_in(f)] + notes
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            for rel in folders + notes:
                dest = self.root / TRASH / stamp / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(self.root / rel, dest)
        return stamp, sorted(moved)

    def restore_batch(self, trash_id: str) -> tuple[list[str], list[str]]:
        """Put back everything deleted together. Files whose place is taken again are left in
        the trash. Returns (restored note paths, skipped paths)."""
        if not re.fullmatch(r"[0-9\-]+", trash_id or ""):
            raise InvalidPath("Bad trash id")
        base = self.root / TRASH / trash_id
        if not base.is_dir():
            raise NoteMissing(trash_id)
        restored, skipped = [], []
        with self._lock:
            for dirpath, dirnames, filenames in os.walk(base, topdown=True):
                here = Path(dirpath)
                rel_dir = here.relative_to(base)
                (self.root / rel_dir).mkdir(parents=True, exist_ok=True)   # empty folders come back too
                for f in filenames:
                    rel = (rel_dir / f).as_posix()
                    if (self.root / rel).exists():
                        skipped.append(rel)
                        continue
                    os.replace(here / f, self.root / rel)
                    if f.lower().endswith(NOTE_SUFFIX):
                        restored.append(rel)
            for dirpath, _, _ in sorted(os.walk(base), key=lambda t: -len(t[0])):   # tidy up emptied folders
                try:
                    Path(dirpath).rmdir()
                except OSError:
                    pass
        return sorted(restored), skipped

    def trashed(self) -> list[dict]:
        base = self.root / TRASH
        out = []
        if base.is_dir():
            for stamp_dir in sorted(base.iterdir(), reverse=True):
                if not stamp_dir.is_dir():
                    continue
                for f in stamp_dir.rglob("*.md"):
                    out.append({"trash_id": stamp_dir.name, "path": f.relative_to(stamp_dir).as_posix()})
        return out

    def restore(self, trash_id: str, rel: str) -> str:
        rel = self.clean_rel(rel)
        if not re.fullmatch(r"[0-9\-]+", trash_id or ""):
            raise InvalidPath("Bad trash id")
        with self._lock:
            src = self.root / TRASH / trash_id / rel
            if not src.is_file():
                raise NoteMissing(rel)
            dst = self.root / rel
            if dst.exists():
                raise NoteExists(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
        return rel
