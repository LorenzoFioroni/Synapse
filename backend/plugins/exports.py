"""Export writers contributed by plugins. Import parsers remain in formats/."""

import re
from collections.abc import Callable
from dataclasses import dataclass

from .errors import PluginError


@dataclass
class ExportFormat:
    id: str
    title: str
    extension: str
    mimetype: str
    fn: Callable[[list[dict], dict], str]


def register(formats: dict, id: str, *, title: str, extension: str, mimetype: str):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", id or "") or id in formats:
        raise PluginError("Export format id must be unique, with lowercase letters, digits and _")
    if not re.fullmatch(r"[a-z0-9]{1,12}", extension) or not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", mimetype):
        raise PluginError("Export format needs a safe extension and MIME type")

    def decorate(fn):
        formats[id] = ExportFormat(id, str(title)[:80], extension, mimetype, fn)
        return fn

    return decorate
