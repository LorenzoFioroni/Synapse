"""Offline scholarly helpers for plugins, shared with Synapse's own importers and lookups.

Import with ``from backend.plugins import scholarly`` (Plugin API 1.4). Identifiers keep the
same normalization rules as the library; author pairs are ``(family, given)`` and serialize as
``Family, Given; Family2, Given2``. No helper makes an HTTP request or reads the user's data.
"""

from ..formats.common import join_authors, parse_display_name, split_authors, strip_tags
from ..library.identifiers import ARXIV_ID, arxiv_id_of, clean_doi, norm_arxiv, norm_doi
from ..online.metadata import TITLE_MATCH, title_similarity

__all__ = [
    "ARXIV_ID",
    "TITLE_MATCH",
    "arxiv_id_of",
    "clean_doi",
    "join_authors",
    "norm_arxiv",
    "norm_doi",
    "parse_display_name",
    "split_authors",
    "strip_tags",
    "title_similarity",
]
