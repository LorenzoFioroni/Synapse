"""Find Available PDF uses a known arXiv id first. The network is faked."""

import time

import pytest

from backend.online import http, pdf_finder
from tests.conftest import make_pdf


class Resp:
    def __init__(self, status=200, content=b"", ctype="application/pdf", data=None, text=""):
        self.status_code, self.content, self._data, self.text = status, content, data, text
        self.headers = {"Content-Type": ctype}

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._data or {}


@pytest.fixture
def web(monkeypatch, tmp_path):
    """routes: {url prefix: Resp or callable}; calls: every URL requested, in order."""
    routes, calls = {}, []

    def get(url, **kw):
        calls.append(url)
        for prefix, r in routes.items():
            if url.startswith(prefix):
                return r() if callable(r) else r
        return Resp(404, ctype="text/html")

    monkeypatch.setattr(pdf_finder.requests, "get", get)
    return routes, calls


def pdf_bytes(tmp_path, text, name="x.pdf"):
    return make_pdf(tmp_path / name, text).read_bytes()


def test_preprint_with_archive_id_gets_the_arxiv_pdf_first(svc, web, tmp_path):
    routes, calls = web
    routes["https://arxiv.org/pdf/1412.6980"] = Resp(
        content=pdf_bytes(tmp_path, "arXiv:1412.6980v9 [cs.LG] 30 Jan 2017")
    )
    item = svc.papers.create(
        {
            "title": "Adam: A Method for Stochastic Optimization",
            "item_type": "preprint",
            "archive_id": "arXiv:1412.6980",
        }
    )
    res = svc.find_pdf(item["id"])
    assert res["status"] == "success" and res["source"] == "https://arxiv.org/pdf/1412.6980"
    assert calls == ["https://arxiv.org/pdf/1412.6980"]  # no Unpaywall or title search
    assert svc.papers.get(item["id"])["attachments"][0]["name"] == "Full Text PDF"


def test_old_style_id(svc, web, tmp_path):
    routes, calls = web
    routes["https://arxiv.org/pdf/quant-ph/0001106"] = Resp(
        content=pdf_bytes(tmp_path, "Quantum Computation by Adiabatic Evolution")
    )
    item = svc.papers.create(
        {"title": "Quantum Computation by Adiabatic Evolution", "archive_id": "arXiv:quant-ph/0001106"}
    )
    assert svc.find_pdf(item["id"])["status"] == "success"
    assert calls[0] == "https://arxiv.org/pdf/quant-ph/0001106"


def test_id_from_an_arxiv_doi(svc, web, tmp_path):
    routes, calls = web
    routes["https://arxiv.org/pdf/2608.27561"] = Resp(content=pdf_bytes(tmp_path, "arXiv:2608.27561v1"))
    item = svc.papers.create(
        {
            "title": "Pulling strings in real time: flux tube dynamics in $\\mathbb{Z}_2$ theories",
            "doi": "10.48550/arXiv.2608.27561",
        }
    )
    assert svc.find_pdf(item["id"])["status"] == "success"
    assert calls == ["https://arxiv.org/pdf/2608.27561"]  # verified by the arXiv stamp, despite the LaTeX title


def test_published_paper_prefers_the_publishers_copy(svc, web, tmp_path):
    routes, calls = web
    routes["https://api.unpaywall.org/v2/10.1103/PhysRevLett.1"] = Resp(
        ctype="application/json", data={"best_oa_location": {"url_for_pdf": "https://journals.example/oa.pdf"}}
    )
    routes["https://journals.example/oa.pdf"] = Resp(content=pdf_bytes(tmp_path, "doi 10.1103/PhysRevLett.1 Published"))
    item = svc.papers.create({"title": "Published", "doi": "10.1103/PhysRevLett.1", "extra": "arXiv: 2301.00001"})
    res = svc.find_pdf(item["id"])
    assert res["source"] == "https://journals.example/oa.pdf"
    assert calls[0].startswith("https://api.unpaywall.org/v2/10.1103/PhysRevLett.1?email=")
    assert calls[1] == "https://journals.example/oa.pdf"


def test_published_paper_falls_back_to_arxiv(svc, web, tmp_path):
    routes, calls = web
    routes["https://api.unpaywall.org/v2/"] = Resp(ctype="application/json", data={"best_oa_location": None})
    routes["https://arxiv.org/pdf/2301.00001"] = Resp(content=pdf_bytes(tmp_path, "arXiv:2301.00001v2 Closed paper"))
    item = svc.papers.create(
        {"title": "Closed paper", "doi": "10.1016/j.x.2023.1", "url": "https://arxiv.org/abs/2301.00001"}
    )
    res = svc.find_pdf(item["id"])
    assert res["status"] == "success" and res["source"] == "https://arxiv.org/pdf/2301.00001"
    assert not any("scholar" in c or "search" in c for c in calls)


def test_title_search_only_when_the_arxiv_copy_is_not_the_paper(svc, web, tmp_path):
    routes, calls = web
    routes["https://arxiv.org/pdf/2401.11111"] = Resp(content=pdf_bytes(tmp_path, "Something else entirely", "a.pdf"))
    routes["https://api.unpaywall.org/v2/search"] = Resp(
        ctype="application/json",
        data={"results": [{"response": {"best_oa_location": {"url_for_pdf": "https://repo.example/right.pdf"}}}]},
    )
    routes["https://repo.example/right.pdf"] = Resp(
        content=pdf_bytes(tmp_path, "A Very Specific Title Of The Right Paper", "b.pdf")
    )
    item = svc.papers.create({"title": "A Very Specific Title Of The Right Paper", "archive_id": "arXiv:2401.11111"})
    res = svc.find_pdf(item["id"])
    assert res["source"] == "https://repo.example/right.pdf"
    assert calls[0] == "https://arxiv.org/pdf/2401.11111" and any("unpaywall.org/v2/search" in c for c in calls[1:])


def test_items_without_arxiv_id_behave_as_before(svc, web, tmp_path):
    routes, calls = web
    item = svc.papers.create({"title": "No identifiers here", "doi": "10.1/abc"})
    res = svc.find_pdf(item["id"])
    assert res["status"] == "not_found"
    assert calls[0].startswith("https://api.unpaywall.org/v2/10.1/abc")
    assert any("unpaywall.org/v2/search" in c for c in calls)
    assert not any("scholar.google" in c for c in calls)  # never asked


def test_arxiv_downloads_are_spaced(svc, web, tmp_path, monkeypatch):
    routes, calls = web
    monkeypatch.setattr(http, "ARXIV_SPACING", 0.3)
    stamps = []

    def arxiv_pdf():
        stamps.append(time.monotonic())
        return Resp(content=pdf_bytes(tmp_path, f"arXiv:2402.0000{len(stamps)}v1", f"{len(stamps)}.pdf"))

    for i in (1, 2, 3):
        routes[f"https://arxiv.org/pdf/2402.0000{i}"] = arxiv_pdf
    ids = [svc.papers.create({"title": f"P{i}", "archive_id": f"arXiv:2402.0000{i}"})["id"] for i in (1, 2, 3)]
    assert all(svc.find_pdf(i)["status"] == "success" for i in ids)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(stamps) == 3 and min(gaps) >= 0.28, gaps


def test_an_arxiv_id_alone_is_enough(svc, web, tmp_path):
    routes, _ = web
    routes["https://arxiv.org/pdf/2403.05147"] = Resp(content=pdf_bytes(tmp_path, "arXiv:2403.05147v1"))
    item = svc.papers.create({"archive_id": "arXiv:2403.05147"})
    assert svc.find_pdf(item["id"])["status"] == "success"


def test_title_search_requests(monkeypatch):
    """The contact e-mail is URL-encoded, and the arXiv search is https, spaced and on TIMEOUT."""
    from backend.config import APP_ROOT
    from backend.plugins.loading import load_module

    calls, waits = [], []

    def get(url, **kw):
        calls.append((url, kw.get("timeout")))
        return Resp(404, ctype="text/html")

    module, _ = load_module("open-access-pdfs", APP_ROOT / "plugins" / "open-access-pdfs" / "server")
    finder = module.finder
    monkeypatch.setattr(finder.requests, "get", get)
    monkeypatch.setattr(finder, "arxiv_wait", lambda: waits.append(len(calls)))
    assert finder.by_identifier("10.1/abc", None, "me+lib@example.org") == []
    assert finder.by_title("A title", "me+lib@example.org") == []
    urls = [u for u, _ in calls]
    assert urls[0] == "https://api.unpaywall.org/v2/10.1/abc?email=me%2Blib@example.org"
    assert urls[1].endswith("&email=me%2Blib@example.org")
    url, timeout = calls[-1]
    assert url.startswith("https://export.arxiv.org/api/query?") and timeout == http.TIMEOUT
    assert waits == [len(calls) - 1]  # right before the arXiv request


def test_sources_come_from_plugins(client, svc, web, tmp_path):
    """Find Available PDF asks the plugins' PDF sources: a plugin can add one, and with the core
    plugin off and no other source it says so."""
    routes, calls = web
    folder = svc.config.plugins_dir / "my-proxy"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        '{"id": "my-proxy", "name": "My Proxy", "version": "1", "api": "^1.0", "backend": "p.py"}'
    )
    (folder / "p.py").write_text(
        "def activate(plugin):\n"
        "    @plugin.pdf_source('mine', title='My library', order=1)\n"
        "    def mine(paper):\n"
        "        return ['https://proxy.example/' + (paper['doi'] or 'none'), 'not a url']\n"
    )
    client.post("/api/plugin-host/my-proxy/enable")
    routes["https://proxy.example/10.1/mine"] = Resp(content=pdf_bytes(tmp_path, "A Paper Found Through The Proxy"))
    item = svc.papers.create({"title": "A Paper Found Through The Proxy", "doi": "10.1/mine"})
    res = svc.find_pdf(item["id"])
    assert res["status"] == "success" and res["source"] == "https://proxy.example/10.1/mine"
    downloads = [c for c in calls if not c.startswith(("https://api.unpaywall.org", "https://export.arxiv.org"))]
    assert downloads[0] == "https://proxy.example/10.1/mine"  # order 1: tried before Unpaywall's copy
    info = next(p for p in client.get("/api/plugin-host").get_json()["plugins"] if p["id"] == "my-proxy")
    assert info["pdf_sources"] == [{"id": "mine", "title": "My library", "by_title": False}]
    client.post("/api/plugin-host/my-proxy/disable")
    client.post("/api/plugin-host/open-access-pdfs/disable")
    calls.clear()
    other = svc.papers.create({"title": "Another", "doi": "10.1/other"})
    res = svc.find_pdf(other["id"])
    assert res["status"] == "not_found" and "Open-Access PDFs" in res["message"] and calls == []


def test_find_pdf_endpoint_accepts_an_arxiv_id_alone(client, svc, web, tmp_path):
    routes, _ = web
    routes["https://arxiv.org/pdf/2403.05147"] = Resp(content=pdf_bytes(tmp_path, "arXiv:2403.05147v1"))
    item = svc.papers.create({"archive_id": "arXiv:2403.05147"})
    r = client.post(f"/api/items/{item['id']}/find_pdf")
    assert r.status_code == 200 and r.get_json()["status"] == "success"
    bare = svc.papers.create({"item_type": "webpage"})
    r = client.post(f"/api/items/{bare['id']}/find_pdf")
    assert r.status_code == 400 and "arXiv ID" in r.get_json()["error"]
