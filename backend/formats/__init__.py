"""Import format registry and shared codecs; plugins register the library export writers.

Each format provides ``parse(text) -> list[dict]`` and
``dump(items, **options) -> str`` working on the shared item dict
representation (see ``common.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import bibtex, csljson, endnote, ris


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    extension: str
    mimetype: str
    parse: Callable[[str], list]
    dump: Callable[..., str]
    sniff: Callable[[str], bool]


FORMATS: dict[str, Format] = {
    f.key: f
    for f in [
        Format("bibtex", "BibTeX", "bib", "application/x-bibtex", bibtex.parse, bibtex.dump, bibtex.sniff),
        Format("ris", "RIS", "ris", "application/x-research-info-systems", ris.parse, ris.dump, ris.sniff),
        Format(
            "endnote",
            "EndNote (tagged .enw)",
            "enw",
            "application/x-endnote-refer",
            endnote.parse,
            endnote.dump,
            endnote.sniff,
        ),
        Format(
            "endnote_xml",
            "EndNote XML",
            "xml",
            "application/xml",
            endnote.parse_xml,
            endnote.dump_xml,
            endnote.sniff_xml,
        ),
        Format("csljson", "CSL JSON", "json", "application/json", csljson.parse, csljson.dump, csljson.sniff),
    ]
}

EXTENSIONS = {
    "bib": "bibtex",
    "bibtex": "bibtex",
    "ris": "ris",
    "enw": "endnote",
    "txt": None,
    "xml": "endnote_xml",
    "json": "csljson",
}


class FormatError(ValueError):
    pass


def detect(text: str, filename: str | None = None) -> str:
    """Guess the format from the content (and the file extension as a tie-breaker)."""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    hinted = EXTENSIONS.get(ext)
    if hinted and FORMATS[hinted].sniff(text):
        return hinted
    for key in ("endnote_xml", "csljson", "ris", "endnote", "bibtex"):
        if FORMATS[key].sniff(text):
            return key
    if hinted:
        return hinted
    raise FormatError("Could not recognise the format. Supported: BibTeX, RIS, EndNote (.enw / XML), CSL JSON.")


def parse(text: str, fmt: str | None = None, filename: str | None = None) -> tuple[str, list[dict]]:
    fmt = fmt or detect(text, filename)
    if fmt not in FORMATS:
        raise FormatError(f"Unknown format: {fmt}")
    try:
        items = FORMATS[fmt].parse(text)
    except Exception as e:
        raise FormatError(f"Could not read {FORMATS[fmt].label}: {e}") from e
    return fmt, items


def dump(items: list[dict], fmt: str, **options) -> str:
    if fmt not in FORMATS:
        raise FormatError(f"Unknown format: {fmt}")
    return FORMATS[fmt].dump(items, **options)


def listing() -> list[dict]:
    return [{"key": f.key, "label": f.label, "extension": f.extension} for f in FORMATS.values()]
