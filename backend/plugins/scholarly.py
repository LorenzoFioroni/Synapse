"""Offline scholarly helpers for plugins, shared with Synapse's own importers and lookups.

Import with ``from backend.plugins import scholarly`` (Plugin API 1.4). Identifiers keep the
same normalization rules as the library; author pairs are ``(family, given)`` and serialize as
``Family, Given; Family2, Given2``. No helper makes an HTTP request or reads the user's data.
"""

from ..formats.common import join_authors, parse_display_name, split_authors, strip_tags
from ..library.identifiers import ARXIV_DOI, ARXIV_ID, arxiv_id_of, clean_doi, identifiers, norm_arxiv, norm_doi
from ..online.metadata import TITLE_MATCH, compute_changes, title_similarity
from ..references.store import REF_FIELDS

__all__ = [
    "ARXIV_DOI",
    "ARXIV_ID",
    "REF_FIELDS",
    "TITLE_MATCH",
    "arxiv_id_of",
    "clean_doi",
    "compute_changes",
    "identifiers",
    "join_authors",
    "norm_arxiv",
    "norm_doi",
    "parse_display_name",
    "split_authors",
    "strip_tags",
    "title_similarity",
]
