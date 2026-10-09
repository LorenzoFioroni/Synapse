"""Front-matter: the ``---`` delimited header at the top of a note.

Only a small, predictable subset of YAML is understood:

    key: value              a scalar (optionally "double" or 'single' quoted)
    key: [a, b, "c d"]      an inline list
    key:                    a block list
      - a
      - b

Anything else (nested maps, multi-line strings, …) is kept verbatim as a raw string and
written back untouched. Editing goes through ``set_fields``, which rewrites only the keys
it is given and leaves every other line of the file byte-for-byte as it was.
"""
from __future__ import annotations

import re

_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-]*)\s*:(.*)$")
_PLAIN_SAFE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .,:/@+\-]*$")


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text[:2000] else "\n"


def split(text: str) -> tuple[str | None, str, int]:
    """Return ``(header, body, body_start_line)``.

    ``header`` is the text between the delimiters (None when the note has no front-matter),
    ``body_start_line`` the 0-based line number of the first body line, so positions found
    in the body can be reported as line numbers of the whole file.
    """
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return None, text, 0
    lines = text.splitlines(keepends=True)
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") in ("---", "..."):
            return "".join(lines[1:i]), "".join(lines[i + 1:]), i + 1
    return None, text, 0  # unterminated: treat the whole file as body


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def _split_inline_list(inner: str) -> list[str]:
    items, cur, quote = [], "", None
    for ch in inner:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch == ",":
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    return [_unquote(i) for i in items if i.strip()]


def _strip_comment(v: str) -> str:
    # " # comment" outside quotes ends the value
    quote = None
    for i, ch in enumerate(v):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or v[i - 1] in " \t"):
            return v[:i].rstrip()
    return v


def _entries(header: str) -> list[tuple[str | None, list[str]]]:
    """Group header lines into (key, lines) entries; comment/blank lines get key None."""
    out: list[tuple[str | None, list[str]]] = []
    for line in header.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        m = _KEY.match(bare)
        if m and not bare.startswith((" ", "\t")):
            out.append((m.group(1), [line]))
        elif out and out[-1][0] is not None and (bare.startswith((" ", "\t", "-")) and bare.strip()):
            out[-1][1].append(line)  # continuation (block list item or indented value)
        else:
            out.append((None, [line]))
    return out


def _value(lines: list[str]):
    first = _KEY.match(lines[0].rstrip("\r\n")).group(2)
    rest = [l.rstrip("\r\n") for l in lines[1:]]
    v = _strip_comment(first.strip())
    if not rest:
        if v.startswith("[") and v.endswith("]"):
            return _split_inline_list(v[1:-1])
        return _unquote(v)
    if not v and all(l.strip().startswith("- ") or l.strip() == "-" for l in rest):
        return [_unquote(_strip_comment(l.strip()[1:].strip())) for l in rest if l.strip() != "-"]
    return "\n".join([first.strip(), *rest]).strip()  # something richer: keep it raw


def parse(header: str | None) -> dict:
    if not header:
        return {}
    data = {}
    for key, lines in _entries(header):
        if key is not None and key not in data:
            data[key] = _value(lines)
    return data


def read(text: str) -> dict:
    return parse(split(text)[0])


def _scalar(v, *, in_list: bool = False) -> str:
    s = "" if v is None else str(v)
    plain = (s == "" and not in_list) or (
        bool(_PLAIN_SAFE.match(s)) and ": " not in s and not s.endswith(":") and s.strip() == s
        and not (in_list and "," in s))
    if plain:
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def format_value(v) -> str:
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_scalar(i, in_list=True) for i in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return _scalar(v)


def set_fields(text: str, updates: dict) -> str:
    """Return ``text`` with the given front-matter keys set (a value of None removes the key).

    Keys not mentioned, comments and the body are preserved exactly. A note without
    front-matter gets one.
    """
    nl = _newline(text)
    header, body, _ = split(text)
    entries = _entries(header) if header is not None else []
    pending = dict(updates)
    new_lines: list[str] = []
    for key, lines in entries:
        if key is not None and key in pending:
            v = pending.pop(key)
            if v is not None:
                new_lines.append(f"{key}: {format_value(v)}{nl}")
        else:
            new_lines.extend(lines)
    if new_lines and not new_lines[-1].endswith(("\n", "\r")):
        new_lines[-1] += nl
    for key, v in pending.items():
        if v is not None:
            new_lines.append(f"{key}: {format_value(v)}{nl}")
    if not new_lines and header is None:
        return text
    return f"---{nl}{''.join(new_lines)}---{nl}{body}"
