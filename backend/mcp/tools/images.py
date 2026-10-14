"""Images in notes: view_image."""

from __future__ import annotations

import re
import struct

from ...notes import parser
from ...notes.attachments import attachment_name
from ...notes.service import sniff_image
from ...notes.vault import ATTACHMENTS, InvalidPath
from ...pdf import png as pdfpng
from ..protocol import Image, Parts, ToolError
from .common import MAX_IMAGES_PER_VIEW, drop_empty

IMAGE_MAX_SIDE = 1568  # larger images are scaled down (clients shrink them anyway)
IMAGE_MAX_BYTES = 3_500_000  # ~4.7 MB as base64, under the usual 5 MB per image


class ImageToolsMixin:
    """view_image (images of the notes folder)."""

    def _note_images(self, text: str) -> list[dict]:
        """The images a note shows, in order: {line, path (as written), alt, caption}."""
        lines = text.split("\n")
        out = []
        for l in parser.parse(text).links:
            if l.kind != "image":
                continue
            caption = None
            nxt = lines[l.line] if l.line < len(lines) else ""  # the line below (l.line is 1-based)
            m = re.fullmatch(r"\s*\*(.+)\*\s*", nxt)
            if m:
                caption = m.group(1).strip()
            out.append(
                drop_empty(
                    {
                        "line": l.line,
                        "path": l.target,
                        "alt": l.alias or None,
                        "caption": caption,
                        "url": True if re.match(r"^(https?:)?//|^data:", l.target, re.I) else None,
                    }
                )
            )
        return out

    def _image_file(self, target: str):
        """The file of an image path as written in a note (``attachments/x.png``, ``./x.png``, or a
        path the client mangled into an absolute one ending in ``attachments/…``)."""
        t = str(target or "").strip().strip("<>")
        if not t:
            raise ToolError("Give the image path as written in the note, e.g. attachments/x.png.")
        if re.match(r"^(https?:)?//|^data:", t, re.I):
            raise ToolError(
                f"{t[:80]} is an image on the web, not in the notes folder; this tool only shows "
                "images stored with the notes."
            )
        t = t.replace("\\", "/")
        if "/" + ATTACHMENTS + "/" in t:  # e.g. /Users/…/data/notes/attachments/x.png
            t = t[t.rindex("/" + ATTACHMENTS + "/") + 1 :]
        candidates = []
        try:
            candidates.append(self.s.vault.clean_rel(re.sub(r"^(\./)+", "", t), suffix=None))
        except InvalidPath:
            pass
        name = attachment_name(t)
        if name:
            try:
                candidates.append(self.s.vault.clean_rel(f"{ATTACHMENTS}/{name}", suffix=None))
            except InvalidPath:
                pass
        for rel in candidates:
            p = self.s.vault.root / rel
            if p.is_file():
                return rel, p
        raise ToolError(
            f"No image '{target}' in the notes folder"
            + (f" (looked for {candidates[-1]})." if candidates else ".")
            + " Use the path exactly as the note writes it, e.g. attachments/x.png."
        )

    def _image_block(self, rel: str, p) -> tuple[Image, dict]:
        data = p.read_bytes()
        ext = sniff_image(data)
        if not ext:
            raise ToolError(f"{rel} is not a PNG, JPEG, GIF or WebP image.")
        info = {"path": rel, "bytes": len(data)}
        region = pdfpng.read_region(data) if ext == "png" else None
        mime = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}[ext]
        try:
            from PIL import Image as PILImage
        except ImportError:
            PILImage = None
        if PILImage is not None:
            import io

            try:
                im = PILImage.open(io.BytesIO(data))
                info["size"] = f"{im.width}x{im.height}"
                if max(im.size) > IMAGE_MAX_SIDE or len(data) > IMAGE_MAX_BYTES or ext == "gif":
                    im.seek(0)
                    im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
                    im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
                    buf = io.BytesIO()
                    im.save(buf, "PNG", optimize=True)
                    if buf.tell() > IMAGE_MAX_BYTES:  # a big photo: JPEG keeps it under the limit
                        buf = io.BytesIO()
                        im.convert("RGB").save(buf, "JPEG", quality=85)
                        mime = "image/jpeg"
                    else:
                        mime = "image/png"
                    data = buf.getvalue()
                    info["sent_as"] = f"{im.width}x{im.height} (scaled down)"
            except Exception:
                pass
        elif ext == "png" and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            info["size"] = f"{w}x{h}"
        if len(data) > IMAGE_MAX_BYTES:
            raise ToolError(
                f"{rel} is too large to send ({len(data) / 1e6:.1f} MB). Installing Pillow "
                "(pip install pillow) lets this tool scale it down."
            )
        if region:
            info["snapshot_of"] = region
        return Image(data, mime), info

    def view_image(self, path: str | None = None, note: str | None = None):
        if not path and not note:
            raise ToolError("Give path (an image path from a note, e.g. attachments/x.png) and/or note.")
        images: list[dict]
        if note:
            npath = self._note_path(note)
            text = self.s.vault.read(npath)[0]
            images = self._note_images(text)
            if path:
                want = attachment_name(path) or path
                found = [i for i in images if i["path"] == path or attachment_name(i["path"]) == want]
                images = found or [{"path": path}]
            elif not images:
                raise ToolError(f"{npath} shows no images.")
        else:
            images = [{"path": path}]
        parts: list = []
        skipped = []
        for img in images:
            if img.get("url"):
                skipped.append(f"line {img['line']}: {img['path'][:80]} (an image on the web)")
                continue
            if len([x for x in parts if isinstance(x, Image)]) >= MAX_IMAGES_PER_VIEW:
                skipped.append(f"line {img.get('line')}: {img['path']}")
                continue
            rel, p = self._image_file(img["path"])
            block, info = self._image_block(rel, p)
            meta = drop_empty({**{k: img.get(k) for k in ("line", "alt", "caption")}, **info})
            parts += [meta, block]
        if skipped:
            parts.append(
                {
                    "not_shown": skipped,
                    "hint": f"At most {MAX_IMAGES_PER_VIEW} images per call; ask for the others by path."
                    if any("web" not in s for s in skipped)
                    else None,
                }
            )
            parts[-1] = drop_empty(parts[-1])
        if not any(isinstance(x, Image) for x in parts):
            raise ToolError("None of the note's images is stored in the notes folder: " + "; ".join(skipped))
        return Parts(parts)
