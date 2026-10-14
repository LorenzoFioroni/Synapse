"""DOI and arXiv identifiers (backend/library/identifiers.py)."""

from backend.library import identifiers as I


def test_identifiers():
    assert I.identifiers({"doi": "https://doi.org/10.1103/PhysRevX.13.011033"}) == ("10.1103/physrevx.13.011033", None)
    assert I.identifiers({"doi": "10.48550/arXiv.2101.00001"}) == ("10.48550/arxiv.2101.00001", "2101.00001")
    assert I.identifiers({"archive_id": "arXiv:2101.00001v3"}) == (None, "2101.00001")


def test_arxiv_id_of_items():
    assert I.arxiv_id_of({"archive_id": "arXiv:2205.03415"}) == "2205.03415"
    assert I.arxiv_id_of({"url": "https://arxiv.org/abs/2205.03415v3"}) == "2205.03415"
    assert I.arxiv_id_of({"doi": "10.48550/arXiv.2205.03415"}) == "2205.03415"
    assert I.arxiv_id_of({"extra": "arXiv: cond-mat/0601001"}) == "cond-mat/0601001"
    assert I.arxiv_id_of({"title": "nothing", "doi": "10.1103/x"}) is None


def test_title_key():
    assert I.title_key("Matrix-Product  States: <i>a</i> Review!") == I.title_key("matrix product states a review")
    assert I.title_key("Bañuls et al.: $\\mathcal{O}(N)$ scaling") == I.title_key("Banuls et al O N scaling")
    assert I.title_key("Introduction") is None  # too short to trust
