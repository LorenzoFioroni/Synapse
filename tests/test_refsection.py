"""Finding a paper's reference list in its text (backend/pdf/refsection.py), on made-up papers."""

from backend.pdf import refsection
from backend.pdf.text import abstract_from_text

BODY = "A study of transport\nWe follow [1] and compare with earlier work.\n" + "Body text of the paper. " * 300 + "\n"


def numbered(n=30, style="[{}]"):
    return "".join(
        f"{style.format(i)} A. Author and B. Other, J. Phys. {i}, 100 ({1990 + i}).\n" for i in range(1, n + 1)
    )


def test_a_numbered_list_is_found_with_its_heading_and_ends_at_the_appendix():
    appendix = "APPENDIX A: DETAILS\n" + "What Author did is explained here. " * 30
    text = BODY + "References\n" + numbered() + appendix
    ((start, end),) = refsection.reference_spans(text)
    assert text[start:].startswith("References\n[1] A. Author") and text[end:].startswith("APPENDIX A")
    body, refs = refsection.split(text)
    assert "explained here" in body and "J. Phys. 7" in refs and "J. Phys. 7" not in body


def test_a_list_without_a_heading_and_with_nothing_after_it():
    text = BODY + numbered()
    assert refsection.reference_spans(text) == [(len(BODY), len(text))]


def test_other_styles_of_list():
    for style in ("{}.", "[ {} ]"):  # "12. Author", and brackets some PDFs space out
        text = BODY + numbered(style=style)
        assert refsection.reference_spans(text) == [(len(BODY), len(text))], style
    by_name = "".join(f"- Smith, J. A., {1970 + i}, Ann. Phys. {i}, 67.\n" for i in range(20))
    text = BODY + by_name + "FIG. 1. A caption printed after the list, as some preprints do.\n" + "Caption text. " * 20
    ((start, end),) = refsection.reference_spans(text)
    assert start == len(BODY) and text[end:].startswith("FIG. 1.")
    unnumbered = "References\n" + "".join(f"Zhang A, Li B ({1990 + i}) A title. Journal {i}:1-10\n" for i in range(20))
    text = BODY + unnumbered + "Supplementary Material\nMore of the paper.\n"
    ((start, end),) = refsection.reference_spans(text)
    assert start == len(BODY) and text[end:].startswith("Supplementary Material")


def test_a_supplement_has_a_list_of_its_own():
    supplement = "Supplemental Material\n" + "Derivation of the main result. " * 40 + "\n"
    text = BODY + numbered(12) + supplement + numbered(8)
    first, second = refsection.reference_spans(text)
    assert text[first[1] :].startswith("Supplemental Material") and second[1] == len(text)
    assert "Derivation of the main result" in refsection.split(text)[0]


def test_the_body_is_never_taken_for_a_list():
    sections = "".join(f"{i}. Section {i}\n" + "Text of the section. " * 100 + "\n" for i in range(1, 8))
    steps = "".join(f"{i}. Apply the gate and measure the qubit.\n" for i in range(1, 9))  # no years
    equations = "".join(f"({i})\nE = m c^2 is used in what follows.\n" for i in range(1, 9))
    contents = "Contents\nIntroduction 1\nReferences\n79\n"  # a table of contents, at the start
    for text in (sections, BODY + steps + BODY, BODY + equations, contents + BODY):
        assert refsection.reference_spans(text) == []


def test_page_spans():
    refs = numbered()
    pages = [BODY, "Last words of the paper.\n" + refs[:600], refs[600:]]
    spans = refsection.page_spans(pages)
    assert spans[0] == [] and spans[1] == [(25, len(pages[1]))] and spans[2] == [(0, len(pages[2]))]


def test_abstract_after_the_date_line():
    abstract = "We show that the method works for chains of any length. " * 6
    page = (
        "A title\nA. Author and B. Other\nInstitute of Physics\n(Dated: July 1, 2026)\n"
        + abstract.strip()
        + "\nI. INTRODUCTION\nThe body starts here.\n"
    )
    assert abstract_from_text(page) == abstract.strip()
    box = "A title\nReceived: 3 May 2025\nAccepted: 24 November 2025\nCite this article as: Author, A.\n" + abstract
    assert abstract_from_text(box) is None  # a journal's box of dates is not the line under the authors
    wide = "\n".join(["The abstract runs across the whole page, so each of its lines is rather long, as here."] * 6)
    narrow = "\n".join(["The body is set in two columns."] * 12)
    letter = f"A title\nA. Author\n(Received 20 April 2000)\n{wide}\n{narrow}\n"
    assert abstract_from_text(letter) == " ".join(wide.split())


def test_abstract_under_the_authors_without_a_heading_or_a_date_line():
    wide = [
        "We describe a fast adaptive Sampling Technique (FAST) for large sparse Linear Problems",
        "that reorders the unknowns as the iteration proceeds to keep the growth of the fill-in small",
        "while keeping the computational cost low, and we validate it on several standard problems,",
        "where it reaches longer simulation times and a higher precision in the computed observables",
        "than the standard approach.",
    ]
    header = (
        "A long title of a paper that says what the paper is about in many words\n"
        "Ann Author,1 Bob Other,2 and Carla Third 1, 3\n"
        "1Institute of Example Studies, via Esempio 12, 12345 Sampletown, Italy\n"
        "2Laboratoire des Exemples, 54321 Ville-Exemple Cedex, France\n"
    )
    body = "\n".join(["The body of the letter is set in two columns."] * 12)
    assert abstract_from_text(header + "\n".join(wide) + "\n" + body + "\n") == " ".join(wide)
    # the last line of the abstract fills the page: it ends where the narrower columns start
    full = wide[:4]
    full[-1] = "where it reaches longer simulation times and a higher precision in what is computed [1, 2]."
    assert abstract_from_text(header + "\n".join(full) + "\n" + body + "\n") == " ".join(full)
    # a caption, or names set with spaces between the letters, do not start an abstract
    caption = "FIG. 1. The order parameter as a function of the coupling for several sizes of the chain\n" * 4
    assert abstract_from_text(header + caption) is None
    spaced = "Ann Author 1,2 , Bob Other1,2,4,J a n eR o e1,3 & Carla Third 1,2 of the group\n"
    assert abstract_from_text("A title\n" + spaced + "\n".join(wide) + "\n" + body + "\n") == " ".join(wide)
    assert abstract_from_text("A title\nAnn Author\nInstitute of Physics\n") is None
    stamp = "\n".join(wide) + " 1 arXiv:2601.20695v1 [cond-mat.str-el] 27 Jan 2026"
    assert abstract_from_text(header + stamp) == " ".join(wide)  # the stamp up the margin is not part of it
