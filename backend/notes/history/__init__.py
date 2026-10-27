"""The history of the notes' text (Git) and the JSON history kept before it."""

from .git import GitError
from .legacy import LegacyHistory
from .recorder import IDLE, Change, NoteHistory

__all__ = ["IDLE", "Change", "GitError", "LegacyHistory", "NoteHistory"]
