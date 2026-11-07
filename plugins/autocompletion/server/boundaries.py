"""Keep a continuation inside the closing marks already to the right of the cursor."""

from __future__ import annotations

import re

_BOUNDARY = re.compile(r"\${1,2}|\*{1,3}|_{1,3}|~~|\+\+|`+|[)\]}\"']")
_PUNCTUATION = re.compile(r"[\s.,;:!?…]+")
_BRACKETS = {"(": ")", "[": "]", "{": "}"}


def _run(text: str, at: int) -> str:
    end = at + 1
    while end < len(text) and text[end] == text[at]:
        end += 1
    return text[at:end]


def _apostrophe(text: str, at: int, prefix: str) -> bool:
    before = text[at - 1] if at else prefix[-1:]
    after = text[at + 1 : at + 2]
    return bool(before.isalnum() and after.isalnum())


def _escaped(text: str, at: int) -> bool:
    before = at
    while before and text[before - 1] == "\\":
        before -= 1
    return (at - before) % 2 == 1


def trim(text: str, prefix: str, suffix: str, *, fim: bool) -> str:
    """A leading suffix closer belongs to the note, not to the generated insertion.

    New brackets, code spans and math in the insertion can contain that same character. Track
    those locally rather than stopping at its first occurrence or stripping every shared tail.
    """
    rest = suffix.split("\n", 1)[0].lstrip(" \t")
    match = _BOUNDARY.match(rest)
    if not match:
        # Bare punctuation can also be supplied by the model. Only remove a terminal overlap:
        # an earlier period may be a decimal point or part of an abbreviation.
        if rest and (_PUNCTUATION.fullmatch(rest) or not fim):
            end = text.rstrip()
            for size in range(min(len(rest), len(end)), 0, -1):
                at = len(end) - size
                if end.endswith(rest[:size]) and not _escaped(end, at):
                    return end[:at]
        return text

    boundary = match[0]
    brackets: list[str] = []
    emphasis: list[str] = []
    code = ""
    math = ""
    quote = ""
    at = 0
    while at < len(text):
        char = text[at]
        if code:
            if char == "`" and _run(text, at) == code:
                at += len(code)
                code = ""
            else:
                at += len(_run(text, at)) if char == "`" else 1
            continue
        if char == "\\":
            at += 2
            continue
        if math:
            if char == "$" and text.startswith(math, at):
                at += len(math)
                math = ""
            else:
                at += 1
            continue
        if quote:
            if char == quote and not (char == "'" and _apostrophe(text, at, prefix)):
                quote = ""
            at += 1
            continue

        if boundary.startswith("$"):
            # The suffix itself is the enclosing math delimiter; dollars escaped above are literal.
            if text.startswith(boundary, at):
                return text[:at]
            at += 1
            continue
        if char == "`":
            mark = _run(text, at)
            if mark == boundary:
                return text[:at]
            code = mark
            at += len(mark)
            continue
        if char == "$":
            math = "$$" if text.startswith("$$", at) else "$"
            at += len(math)
            continue
        if char in _BRACKETS:
            brackets.append(_BRACKETS[char])
        elif char in _BRACKETS.values():
            if brackets and char == brackets[-1]:
                brackets.pop()
            elif char == boundary and not brackets:
                return text[:at]
        elif char in "\"'":
            apostrophe = char == "'" and _apostrophe(text, at, prefix)
            if char == boundary and not apostrophe:
                return text[:at]
            # Quoted bracket characters do not close an outer parenthesis, e.g. ('text )').
            before = text[at - 1] if at else prefix[-1:]
            if boundary in _BRACKETS.values() and not apostrophe and not before.isalnum():
                quote = char
        elif char in "*_~+":
            mark = _run(text, at)
            after = text[at + len(mark) : at + len(mark) + 1]
            before = text[at - 1] if at else prefix[-1:]
            if char == "_" and before.isalnum() and after.isalnum():
                at += len(mark)
                continue
            if emphasis and mark.startswith(emphasis[-1]):
                at += len(emphasis.pop())
                continue
            if mark == boundary or (len(mark) == 3 and mark.startswith(boundary)):
                return text[:at]
            valid = mark in ("*", "**", "***", "_", "__", "___", "~~", "++")
            intraword = char == "_" and before.isalnum()
            if valid and after and not after.isspace() and not intraword:
                emphasis.append(mark)
            at += len(mark)
            continue
        at += 1
    return text
