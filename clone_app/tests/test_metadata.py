import pytest

from backend import metadata


def test_classify_identifier():
    assert metadata.classify_identifier("https://doi.org/10.1103/PhysRevX.13.011033") == ("doi", "10.1103/PhysRevX.13.011033")
    assert metadata.classify_identifier("arXiv:2205.03415v2") == ("arxiv", "2205.03415")
    assert metadata.classify_identifier("https://arxiv.org/abs/2205.03415") == ("arxiv", "2205.03415")
    assert metadata.classify_identifier("978-0-262-03384-8") == ("isbn", "9780262033848")
    with pytest.raises(metadata.LookupError_):
        metadata.classify_identifier("hello world")


def test_resolve_authors_prefers_known_accents():
    known = [("Bañuls", "Mari Carmen"), ("Žnidarič", "Marko")]
    out = metadata.resolve_authors([("Banuls", "Mari Carmen"), ("Znidaric", "M."), ("Cui", "Jian")], known)
    # exact given-name match only: "M." is not expanded to "Marko"
    assert out == "Bañuls, Mari Carmen; Znidaric, M.; Cui, Jian"


class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.ok = data, status, status < 400

    def json(self):
        return self._d

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


CROSSREF = {"message": {
    "type": "journal-article", "title": ["Superdiffusive Energy Transport"], "DOI": "10.1103/PhysRevX.13.011033",
    "author": [{"family": "Ljubotina", "given": "Marko"}, {"family": "Papic", "given": "Zlatko"}],
    "container-title": ["Physical Review X"], "published-print": {"date-parts": [[2023, 2]]},
    "volume": "13", "issue": "1", "page": "011033", "abstract": "<jats:p>We study transport.</jats:p>",
    "ISSN": ["2160-3308"], "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}]}}


def test_lookup_doi(monkeypatch):
    def fake_get(url, **kw):
        if "crossref" in url:
            return FakeResp(CROSSREF)
        return FakeResp({}, 404)  # OpenAlex unavailable
    monkeypatch.setattr(metadata.requests, "get", fake_get)
    item = metadata.lookup(doi="https://doi.org/10.1103/PhysRevX.13.011033", known=[("Papić", "Zlatko")])
    assert item["title"] == "Superdiffusive Energy Transport"
    assert item["authors"] == "Ljubotina, Marko; Papić, Zlatko"
    assert item["year"] == "2023" and item["abstract"] == "We study transport."
    assert item["journal"] == "Physical Review X" and item["issn"] == "2160-3308"


def test_api_lookup_and_from_identifier(client, monkeypatch):
    monkeypatch.setattr(metadata.requests, "get",
                        lambda url, **kw: FakeResp(CROSSREF) if "crossref" in url else FakeResp({}, 404))
    r = client.post("/api/items/from_identifier", json={"identifier": "10.1103/PhysRevX.13.011033"})
    assert r.status_code == 201
    item = r.get_json()
    assert item["citation_key"] == "ljubotinaSuperdiffusive2023"
    # adding again reports the duplicate
    r2 = client.post("/api/items/from_identifier", json={"identifier": "10.1103/PhysRevX.13.011033"})
    assert r2.status_code == 409 and r2.get_json()["duplicate_of"] == item["id"]
    client.patch(f"/api/items/{item['id']}", json={"title": "old title"})
    look = client.post(f"/api/items/{item['id']}/lookup").get_json()
    assert look["current"]["title"] == "old title" and look["fetched"]["title"] == "Superdiffusive Energy Transport"
