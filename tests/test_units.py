import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from zcleaner import filenames, latex  # noqa: E402
from zcleaner import textutil as tu  # noqa: E402
from zcleaner.duplicates import find_duplicates  # noqa: E402

ROUNDTRIP = ["Schrödinger equation", "Erdős–Rényi graphs", "Łódź and Ångström", "naïve Bayes — a “study”",
             "α-helix of β₂ and CO₂", "x² + y² ≤ 1", "Beyond 100% & more #1 my_var", "Ệ ǿ í ı",
             "Tōkyō, Çağrı Şahin, Dvořák", "Müller 1–10…", "Αθήνα Москва 東京", "Gödel, Escher, Bach"]


@pytest.mark.parametrize("s", ROUNDTRIP)
def test_latex_roundtrip(s):
    enc = latex.to_latex(s)
    assert latex.to_unicode(enc) == s
    assert latex.check(enc) == []
    assert latex.to_latex(enc) == enc  # idempotent: never double-encodes


def test_latex_preserves_existing():
    s = r"The {\"o} and $\alpha_1$ {DNA} \textit{in vivo} 50\% \& more"
    assert latex.to_latex(s) == s
    assert latex.to_unicode(s) == "The ö and α₁ DNA in vivo 50% & more"


def test_latex_decode_variants():
    assert latex.to_unicode(r"\"{o} \"o {\"o} \v{c} {\v c} \'{\i} \ss{} {\o}") == "ö ö ö č č í ß ø"
    assert latex.to_unicode(r"$\mathrm{H}_2\mathrm{O}$ and $^{13}$C") == "H₂O and ¹³C"


def test_latex_check():
    assert "unbalanced braces (1 unclosed)" in latex.check("a {b")
    assert any("&" in p for p in latex.check("a & b"))
    assert any("$" in p for p in latex.check("a $x"))


def test_latex_arg_style():
    assert latex.to_latex("ö", style="arg") == r'\"{o}'


def test_html_to_latex():
    out, cx = tu.html_to_latex("CO<sub>2</sub> in <i>E. coli</i> &amp; Na<sup>+</sup>")
    assert out == r"CO$_{2}$ in \textit{E. coli} \& Na$^{+}$" and not cx


def test_sentence_case():
    t = r"DEEP LEARNING FOR {DNA} SEQUENCING: A REVIEW OF CNN METHODS IN SCHR{\"O}DINGER MODELS"
    out = tu.to_sentence_case(t, from_all_caps=True, proper_nouns={"schrödinger"})
    assert out == r"Deep learning for {DNA} sequencing: A review of CNN methods in Schr{\"o}dinger models"
    assert tu.to_sentence_case("The iPhone and LaTeX in Practice") == "The iPhone and LaTeX in practice"


def test_names():
    assert tu.name_proper_case("VAN DER BERG") == "van der Berg"
    assert tu.name_proper_case("MCDONALD") == "McDonald"
    assert tu.normalize_initials("J.R.") == "J. R."
    assert tu.normalize_initials("J.-P.") == "J.-P."
    assert tu.split_single_field_name("Smith, John")[:2] == ("Smith", "John")
    assert tu.split_single_field_name("ATLAS Collaboration") is None


def test_identifiers():
    assert tu.clean_doi("https://doi.org/10.1000/xyz123.") == "10.1000/xyz123"
    assert tu.clean_doi("doi:10.1000/ABC") == "10.1000/ABC"
    assert tu.arxiv_from("https://arxiv.org/pdf/2101.00001v3.pdf") == "2101.00001"
    assert tu.arxiv_from("10.48550/arXiv.2101.00001") == "2101.00001"
    assert tu.issn_valid("0028-0836") and not tu.issn_valid("0028-0837")
    assert tu.isbn_valid("978-3-16-148410-0") and tu.isbn_valid("0-306-40615-2")
    assert tu.normalize_pages("pp. 123 - 145") == "123--145"
    assert tu.clean_url("https://a.org/x?utm_source=t&id=1") == "https://a.org/x?id=1"


def test_filenames():
    data = {"title": r"The {\"o}ffentliche $\alpha$-helix: part one", "creators": [
        {"creatorType": "author", "lastName": "M{\\\"u}ller", "firstName": "A"},
        {"creatorType": "author", "lastName": "Smith", "firstName": "B"},
        {"creatorType": "author", "lastName": "Doe", "firstName": "C"}], "date": "2020-01-02"}

    class S:
        def get(self, k):
            return {"filename_template": "{firstCreator} - {year} - {title}", "ascii_filenames": True,
                    "max_title_words": 12, "max_length": 120}
    assert filenames.filename_for(data, {}, "x.PDF", S()) == "Muller et al. - 2020 - The offentliche alpha-helix - part one.pdf"
    assert filenames.render("{firstCreator} - {year} - {title}", {"firstCreator": "A", "title": "T"}) == "A - T"


def _it(key, title, year="2020", last="Smith", doi="", typ="journalArticle"):
    return {"key": key, "itemType": typ, "deleted": False, "meta": {},
            "data": {"title": title, "date": year, "DOI": doi, "creators": [{"creatorType": "author", "lastName": last}]}}


def test_duplicates_guards():
    items = [_it("A", "Quantum field theory in curved spacetime, part I"), _it("B", "Quantum field theory in curved spacetime, part II"),
             _it("C", "A study of graphs"), _it("D", "A Study of Graphs."),
             _it("E", "Erratum: Deep nets for cats"), _it("F", "Deep nets for cats"),
             _it("G", "Same title different works", doi="10.1234/a"), _it("H", "Same title different works", doi="10.1234/b"),
             _it("I", "Schr{\\\"o}dinger bridges for everyone"), _it("J", "Schrödinger bridges for everyone")]
    groups = {tuple(g["keys"]) for g in find_duplicates(items)}
    assert ("C", "D") in groups and ("I", "J") in groups
    assert ("A", "B") not in groups and ("E", "F") not in groups and ("G", "H") not in groups


@pytest.mark.parametrize("title,expected", [
    ("Chiral graviton modes in non-Abelian lattice fractional quantum hall states", None),
    ("Many-body magic via Pauli-Markov chains---from criticality to gauge theories", None),
    ("Local vs nonlocal dynamics in cavity-coupled Rydberg atom arrays", None),
    ("i-SPin 2", None),
    ("A sequence of approximated solutions to the S-K model for spin glasses", None),
    ("Efficiency of quantum vs. classical annealing in nonconvex learning problems", None),
    ("Entanglement -- nonstabilizerness separation in hybrid quantum circuits", None),
    ("Quantum Monte Carlo and perturbative study of two-dimensional Bose-Fermi mixtures", None),
    ("Many-Body Quantum Magic", "Many-body quantum magic"),
    ("BCS-BEC Crossover at Finite Temperature for Superfluid Trapped Fermi Atoms",
     "BCS-BEC crossover at finite temperature for superfluid trapped Fermi atoms"),
    ("GPUE: graphics processing unit gross--pitaevskii equation solver", "GPUE: Graphics processing unit Gross--Pitaevskii equation solver"),
    ("An Asymmetry Lower Bound on Fermionic Non-Gaussianity", "An asymmetry lower bound on fermionic non-Gaussianity"),
    ("Entanglement Dynamics and Page Curves in Random Permutation Circuits", "Entanglement dynamics and Page curves in random permutation circuits"),
    ("X-Ray Diffraction of Crystals", "X-ray diffraction of crystals"),
])
def test_sentence_case_keeps_proper_nouns(title, expected):
    assert tu.to_sentence_case(title) == (expected or title)


IDEMPOTENCY_CORPUS = [
    "DEEP LEARNING FOR {DNA} SEQUENCING: A REVIEW OF CNN METHODS", "Many-Body Quantum Magic",
    "A sequence of approximated solutions to the S-K model for spin glasses", "i-SPin 2: An integrator",
    "Efficiency of quantum vs. classical annealing", "Entanglement -- nonstabilizerness separation",
    "GPUE: graphics processing unit gross--pitaevskii equation solver", "X-Ray Diffraction of Crystals",
    "Smart grid — the new and improved power grid: a survey", "Schrödinger meets α-helix & CO₂",
    "The One-Dimensional Ising Model With a Transverse Field", "quTARANG: a python GPE solver",
    "Chiral graviton modes in non-Abelian lattice fractional quantum hall states", "BEC2HPC: A HPC solver",
    "Nonlinear Sigma Models for Monitored Dynamics of Free Fermions.", "Colloquium: Quantum annealing",
]


@pytest.mark.parametrize("t", IDEMPOTENCY_CORPUS)
def test_normalisers_are_idempotent(t):
    """f(f(x)) == f(x): the precondition for every run converging."""
    for f in (lambda x: tu.to_sentence_case(x), tu.to_title_case, latex.to_latex, tu.collapse_ws,
              lambda x: tu.to_sentence_case(latex.to_latex(x))):
        once = f(t)
        assert f(once) == once, (t, once, f(once))
    for f, x in ((tu.normalize_pages, "pp. 12 - 19"), (tu.clean_url, "https://a.org/x?utm_source=t&id=1"),
                 (tu.normalize_initials, "J.R."), (tu.name_proper_case, "VAN DER BERG"), (tu.clean_doi, "https://doi.org/10.1/x.")):
        assert f(f(x)) == f(x)
