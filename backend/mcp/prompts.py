"""Ready-made requests for the AI app's prompt picker (MCP ``prompts``).

Each one tells the model which of the library tools to use and how to cite, so a user need not
phrase the workflow themselves. The text is the user's request: nothing here runs by itself.
"""

from __future__ import annotations

from .protocol import Prompt

CITING = (
    "Cite every claim as [@key, p. N] with the PDF page the tools report. Use a block quote only for exact "
    "words copied from read_paper."
)


def _focus(text: str, how: str) -> str:
    return f" {how} {text}." if text else ""


def review_folder(folder: str, focus: str = "") -> str:
    return (
        f'Write a literature review of the papers in the folder "{folder}" of my library.'
        f"{_focus(focus, 'Focus on')}\n\n"
        f"1. Check the exact folder name with list_collections, then list its papers with search_library "
        f'(query folder:"{folder}", abstracts=true, sort=year; page with offset until next_offset is gone).\n'
        "2. Group the papers by theme from their titles and abstracts. Do not read every PDF: for the handful "
        "that matter most, use get_paper_outline or search_in_paper and read_paper only the pages you need.\n"
        "3. Write the review: a short overview, one section per theme, then open questions. "
        f"{CITING}\n"
        "4. Show it to me; save it with write_note only if I ask."
    )


def summarise_paper(paper: str) -> str:
    return (
        f"Summarise the paper [@{paper.strip().lstrip('[@').rstrip(']')}] into a note.\n\n"
        "1. get_paper gives its metadata and my existing paper note: build on that note, do not repeat it.\n"
        "2. get_paper_outline, then search_in_paper and read_paper for the main result, the method and the "
        "limitations. If a figure or equation carries the result, look at it with view_page and cut it with "
        "snapshot.\n"
        "3. Write: a one-paragraph summary, the key results, the method, the limitations, and how it connects "
        f"to other papers in my library (search_library). {CITING}\n"
        "4. Save it with write_note and give me its link."
    )


def compare_papers(papers: str, aspect: str = "") -> str:
    return (
        f"Compare these papers from my library: {papers}.{_focus(aspect, 'Compare them on')}\n\n"
        "1. get_paper for each, then search_in_paper and read_paper for the pages that bear on the comparison; "
        "do not read whole papers.\n"
        "2. Give a table of the points on which they differ (rows) against the papers (columns), then a short "
        f"discussion of what explains the differences and which result I should trust for what. {CITING}\n"
        "3. Say clearly where a paper does not address a point, instead of guessing."
    )


def find_missing_literature(scope: str = "") -> str:
    where = f"around these papers: {scope}" if scope else "in my library as a whole"
    call = f"get_citation_graph(papers=[{scope}], limit=15)" if scope else "get_citation_graph(limit=20)"
    return (
        f"Find important works I am missing {where}.\n\n"
        f"1. Run {call}: the works cited by several papers but absent from the library are the candidates. "
        "Papers whose reference lists were never fetched contribute nothing, so mention that if it applies.\n"
        "2. For the top candidates give title, authors, year, DOI or arXiv id, and which of my papers cite them "
        "(use get_paper on those for context if useful).\n"
        "3. Rank them by how many of my papers cite them, and say what each is likely to add. You cannot add "
        "papers to the library: list what I should look up."
    )


PROMPTS = [
    Prompt(
        "review_folder",
        "Review a folder",
        "Write a literature review of the papers in a library folder.",
        [
            {"name": "folder", "description": 'Folder name or path, e.g. "Physics/Transport".', "required": True},
            {"name": "focus", "description": "A question or theme to focus on (optional)."},
        ],
        review_folder,
    ),
    Prompt(
        "summarise_paper",
        "Summarise a paper into a note",
        "Summarise one paper with page citations and save it as a note.",
        [{"name": "paper", "description": "Citation key, e.g. smith2021.", "required": True}],
        summarise_paper,
    ),
    Prompt(
        "compare_papers",
        "Compare papers",
        "Compare several papers point by point, with page citations.",
        [
            {"name": "papers", "description": "Citation keys separated by commas.", "required": True},
            {"name": "aspect", "description": "What to compare them on (optional)."},
        ],
        compare_papers,
    ),
    Prompt(
        "find_missing_literature",
        "Find missing literature",
        "List works that several library papers cite but the library lacks.",
        [{"name": "scope", "description": "Citation keys separated by commas (default: the whole library)."}],
        find_missing_literature,
    ),
]
