"""Find Available PDF: the publisher's copy first, then open-access copies; a known arXiv id
before any title search. The network is faked."""

import json
import time

import pytest
from requests.cookies import RequestsCookieJar, create_cookie

from backend.online import article_page, http
from backend.plugins import http as plugin_http
from tests.conftest import make_pdf


class Resp:
    def __init__(self, status=200, content=b"", ctype="application/pdf", data=None, text="", cookies=None):
        self.status_code, self.content, self._data, self.text = status, content, data, text
        self.cookies = cookies or RequestsCookieJar()
        self.headers = {"Content-Type": ctype}
        self.encoding, self.reason = "utf-8", ""
        if data is not None and not content:  # plugin.http reads the body, not .json()
            self.content = json.dumps(data).encode()

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._data or {}

    def iter_content(self, size):
        return [self.content]

    def close(self):
        pass

    @property
    def is_redirect(self):
        return False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Web(tuple):
    """(routes, calls) as before, and ``sent``: the cookies each request carried."""

    def __new__(cls, routes, calls, sent):
        web = super().__new__(cls, (routes, calls))
        web.sent = sent
        return web


@pytest.fixture
def web(monkeypatch, tmp_path):
    """routes: {url prefix: Resp or callable}; calls: every URL requested, in order."""
    routes, calls, sent = {}, [], []  # sent: the cookies each request carried

    def get(url, **kw):
        calls.append(url)
        sent.append(dict(kw.get("cookies") or {}))
        for prefix, r in routes.items():
            if url.startswith(prefix):
                return r() if callable(r) else r
        return Resp(404, ctype="text/html")

    def request(method, url, **kw):  # plugins' requests (plugin.http)
        return get(url, **kw)

    monkeypatch.setattr(http, "public_request", request)  # no HTTP or DNS in tests
    return Web(routes, calls, sent)


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


def test_published_paper_takes_the_open_access_copy_when_the_publisher_refuses(svc, web, tmp_path):
    """Off the university's network the DOI page ends at a login page: Unpaywall's copy next."""
    routes, calls = web
    routes["https://doi.org/10.1103/PhysRevLett.1"] = Resp(ctype="text/html", content=b"<html>Log in</html>")
    routes["https://api.unpaywall.org/v2/10.1103/PhysRevLett.1"] = Resp(
        ctype="application/json", data={"best_oa_location": {"url_for_pdf": "https://journals.example/oa.pdf"}}
    )
    routes["https://journals.example/oa.pdf"] = Resp(content=pdf_bytes(tmp_path, "doi 10.1103/PhysRevLett.1 Published"))
    item = svc.papers.create({"title": "Published", "doi": "10.1103/PhysRevLett.1", "extra": "arXiv: 2301.00001"})
    res = svc.find_pdf(item["id"])
    assert res["source"] == "https://journals.example/oa.pdf"
    assert calls[0].startswith("https://api.crossref.org/works/10.1103/PhysRevLett.1")
    assert calls[1].startswith("https://api.unpaywall.org/v2/10.1103/PhysRevLett.1?email=")
    assert calls[2:] == ["https://doi.org/10.1103/PhysRevLett.1", "https://journals.example/oa.pdf"]


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
    assert calls[0].startswith("https://api.crossref.org/works/10.1/abc")
    assert calls[1].startswith("https://api.unpaywall.org/v2/10.1/abc")
    assert "https://doi.org/10.1/abc" in calls
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


def test_title_search_requests(svc, web):
    """The plugin asks only through plugin.http: the contact e-mail URL-encoded, a DOI quoted into
    Unpaywall's path, and the arXiv search over https in arXiv's line (shared with Synapse)."""
    from backend.config import APP_ROOT
    from backend.plugins.loading import load_module

    _, calls = web
    module, _ = load_module("open-access-pdfs", APP_ROOT / "plugins" / "open-access-pdfs" / "server")
    finder = module.finder
    client = plugin_http.PluginHttp("open-access-pdfs", ["api.unpaywall.org", "export.arxiv.org"], lambda: "test")
    lines = []
    monkeypatch_wait = http.HOST_GAPS.wait
    try:
        http.HOST_GAPS.wait = lambda key, gap: (lines.append((key, len(calls))), monkeypatch_wait(key, gap))
        assert finder.by_identifier(client, "10.1/a?b", None, "me+lib@example.org") == []
        assert finder.by_title(client, "A title", "me+lib@example.org") == []
    finally:
        http.HOST_GAPS.wait = monkeypatch_wait
    assert calls[0] == "https://api.unpaywall.org/v2/10.1/a%3Fb?email=me%2Blib%40example.org"
    assert calls[1].startswith("https://api.unpaywall.org/v2/search?query=A+title&")
    assert calls[1].endswith("email=me%2Blib%40example.org")
    assert calls[2].startswith("https://export.arxiv.org/api/query?search_query=ti%3A%22A+title%22")
    assert ("arxiv.org", 2) in lines  # right before the arXiv request, in arXiv's line


def test_the_plugin_uses_only_the_plugin_api(svc):
    """Open-Access PDFs is a contributed plugin that starts on by itself."""
    info = next(p for p in svc.plugins.listing() if p["id"] == "open-access-pdfs")
    assert info["enabled"] and not info["core"]
    assert info["permissions"]["network"] == ["api.unpaywall.org", "export.arxiv.org"]


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
    apis = ("https://api.unpaywall.org", "https://export.arxiv.org", "https://api.crossref.org")
    downloads = [c for c in calls if not c.startswith(apis)]
    assert downloads[0] == "https://proxy.example/10.1/mine"  # order 1: tried before Unpaywall's copy
    info = next(p for p in client.get("/api/plugin-host").get_json()["plugins"] if p["id"] == "my-proxy")
    assert info["pdf_sources"] == [{"id": "mine", "title": "My library", "by_title": False}]
    client.post("/api/plugin-host/my-proxy/disable")
    client.post("/api/plugin-host/open-access-pdfs/disable")
    client.post("/api/plugin-host/publisher-pdfs/disable")
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


# The publisher's copy (plugins/publisher-pdfs)


def crossref(*links):
    return Resp(ctype="application/json", data={"message": {"link": list(links)}})


def test_the_publishers_pdf_comes_before_the_open_access_copy(svc, web, tmp_path):
    """On a university's network the publisher hands over the version of record: it wins over
    Unpaywall's copy and the arXiv preprint."""
    routes, calls = web
    vor = "https://link.springer.example/content/pdf/10.1007/s1.pdf"
    routes["https://api.crossref.org/works/10.1007/s1"] = crossref(
        {"URL": vor, "content-type": "application/pdf", "content-version": "vor"}
    )
    routes["https://api.unpaywall.org/v2/10.1007/s1"] = Resp(
        ctype="application/json", data={"best_oa_location": {"url_for_pdf": "https://repo.example/am.pdf"}}
    )
    routes[vor] = Resp(content=pdf_bytes(tmp_path, "https://doi.org/10.1007/s1 The Published One"))
    item = svc.papers.create({"title": "The Published One", "doi": "10.1007/s1", "extra": "arXiv: 2301.00002"})
    res = svc.find_pdf(item["id"])
    assert res["status"] == "success" and res["source"] == vor
    downloads = [c for c in calls if not c.startswith(("https://api.", "https://export."))]
    assert downloads == [vor]  # the open-access copies were never needed


def test_the_doi_page_leads_to_the_pdf_it_names(svc, web, tmp_path):
    """Without a Crossref link: doi.org, the article page's citation_pdf_url, the PDF; the cookie
    the page set goes along to the PDF."""
    routes, calls = web
    jar = RequestsCookieJar()
    jar.set_cookie(create_cookie("session", "abc", domain="journals.aps.example"))
    routes["https://doi.org/10.1103/PhysRevX.9"] = Resp(
        ctype="text/html",
        cookies=jar,
        content=b'<html><head><meta name="citation_pdf_url" content="/prx/pdf/10.1103/PhysRevX.9"></head></html>',
    )
    routes["https://doi.org/prx/pdf/"] = Resp(content=pdf_bytes(tmp_path, "10.1103/PhysRevX.9"))
    item = svc.papers.create({"title": "An APS paper", "doi": "10.1103/PhysRevX.9"})
    res = svc.find_pdf(item["id"])
    assert res["status"] == "success" and res["source"] == "https://doi.org/10.1103/PhysRevX.9"
    i = calls.index("https://doi.org/prx/pdf/10.1103/PhysRevX.9")
    assert web.sent[i] == {"session": "abc"}
    assert not any("unpaywall.org/v2/search" in c for c in calls)


def test_a_refresh_page_is_followed_to_the_article(svc, web, tmp_path):
    """Elsevier's DOIs go through a page that moves on with a meta refresh."""
    routes, calls = web
    routes["https://doi.org/10.1016/j.a.1"] = Resp(
        ctype="text/html",
        content=b'<meta HTTP-EQUIV="REFRESH" content="2; url=\'https://hub.example/retrieve?Redirect=x\'">',
    )
    routes["https://hub.example/retrieve"] = Resp(
        ctype="text/html", content=b'<meta name="citation_pdf_url" content="https://sd.example/pdfft?pid=main.pdf">'
    )
    routes["https://sd.example/pdfft"] = Resp(content=pdf_bytes(tmp_path, "doi:10.1016/j.a.1"))
    item = svc.papers.create({"title": "Elsevier", "doi": "10.1016/j.a.1"})
    assert svc.find_pdf(item["id"])["status"] == "success"
    assert "https://sd.example/pdfft?pid=main.pdf" in calls


def test_pages_that_point_at_each_other_end(svc, web):
    routes, calls = web
    routes["https://doi.org/10.1/loop"] = Resp(
        ctype="text/html", content=b'<meta name="citation_pdf_url" content="https://doi.org/10.1/loop">'
    )
    item = svc.papers.create({"title": "Loop", "doi": "10.1/loop"})
    assert svc.find_pdf(item["id"])["status"] == "not_found"
    assert calls.count("https://doi.org/10.1/loop") == 1


def test_crossref_links_that_cannot_be_a_readers_pdf_are_left_out(svc, web):
    from backend.config import APP_ROOT
    from backend.plugins.loading import load_module

    routes, calls = web
    routes["https://api.crossref.org/works/10.1002/x"] = crossref(
        {"URL": "https://api.elsevier.com/content/article/PII:1", "content-type": "application/pdf"},
        {"URL": "https://onlinelibrary.wiley.com/doi/pdf/10.1002/x", "content-type": "application/pdf"},
        {"URL": "https://pub.example/am.pdf", "content-type": "application/pdf", "content-version": "am"},
        {"URL": "https://pub.example/x.xml", "content-type": "text/xml"},
    )
    module, _ = load_module("publisher-pdfs", APP_ROOT / "plugins" / "publisher-pdfs" / "server")
    client = plugin_http.PluginHttp("publisher-pdfs", ["api.crossref.org"], lambda: "test")
    got = module.finder.candidates(client, "10.1002/x", "me+lib@example.org")
    assert got == ["https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/x", "https://doi.org/10.1002/x"]
    assert calls == ["https://api.crossref.org/works/10.1002/x?mailto=me%2Blib%40example.org"]
    assert module.finder.candidates(client, "10.48550/arXiv.2301.1", None) == []  # arXiv's own copy
    assert module.finder.candidates(client, None, None) == []


def test_the_publisher_plugin_uses_only_the_plugin_api(svc):
    info = next(p for p in svc.plugins.listing() if p["id"] == "publisher-pdfs")
    assert info["enabled"] and not info["core"]
    assert info["permissions"]["network"] == ["api.crossref.org"]


@pytest.mark.parametrize(
    ("page", "pdf", "refresh"),
    [
        (b'<META NAME="citation_pdf_url" CONTENT="a/b.pdf">', "https://x.example/p/a/b.pdf", None),
        (
            b'<meta property="citation_pdf_url" content="https://cdn.example/1.pdf?x=1&amp;y=2">',
            "https://cdn.example/1.pdf?x=1&y=2",
            None,
        ),
        (b'<link rel="alternate" type="application/pdf" href="/full.pdf">', "https://x.example/full.pdf", None),
        (b'<meta http-equiv=refresh content="0;URL=https%3A%2F%2Fy.example%2Fa">', None, "https://y.example/a"),
        (b'<meta name="citation_pdf_url" content="javascript:alert(1)">', None, None),
        (b"<html><body>Please log in</body></html>", None, None),
        (b"\xff\xfe<meta <<<", None, None),
    ],
)
def test_article_page_links(page, pdf, refresh):
    assert article_page.links(page, "https://x.example/p/index.html") == (pdf, refresh)


def _verification_pdf(tmp_path, pages, *, ligatures=False):
    """Real PDF pages, including line breaks and a Unicode font map for extracted ligatures."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    writer = PdfWriter()
    for i, lines in enumerate(pages):
        page = PdfReader(make_pdf(tmp_path / f"page-{i}.pdf", "")).pages[0]
        content = DecodedStreamObject()
        text = b"BT /F1 12 Tf 20 250 Td "
        for n, line in enumerate(lines.split("\n")):
            if n:
                text += b" 0 -16 Td "
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            text += b"(" + escaped.encode("latin-1") + b") Tj"
        content.set_data(text + b" ET")
        page[NameObject("/Contents")] = content
        if ligatures:
            mapping = {i: i for i in range(32, 127)} | {1: 0xFB03, 2: 0xFB00}
            cmap = DecodedStreamObject()
            entries = "\n".join(f"<{code:02x}> <{unicode:04x}>" for code, unicode in mapping.items())
            cmap.set_data(
                (
                    "/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
                    "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def "
                    "/CMapName /TestMap def /CMapType 2 def "
                    "1 begincodespacerange <00> <ff> endcodespacerange "
                    f"{len(mapping)} beginbfchar\n{entries}\nendbfchar "
                    "endcmap CMapName currentdict /CMap defineresource pop end end"
                ).encode()
            )
            page["/Resources"]["/Font"]["/F1"][NameObject("/ToUnicode")] = cmap
        writer.add_page(page)
    path = tmp_path / "verification.pdf"
    writer.write(path)
    return str(path)


def test_verification_normalizes_real_pdf_ligatures_and_hyphenated_lines(tmp_path):
    from backend.online.pdf_finder import pdf_verification
    from backend.pdf.text import extract_text

    path = _verification_pdf(tmp_path, ["O\x01ce a\x01nity e\x01cient di\x02usion"], ligatures=True)
    assert "\ufb03" in extract_text(path)[0]  # exercise extraction, not a mocked text return
    assert pdf_verification(path, "Office affinity efficient diffusion", None)["status"] == "verified"
    path = _verification_pdf(tmp_path, ["Superconduc-\nting phase transi-\ntions in corre-\nlated materials"])
    assert (
        pdf_verification(path, "Superconducting phase transitions in correlated materials", None)["status"]
        == "verified"
    )


def test_verification_reads_past_publisher_covers_but_stays_bounded(tmp_path):
    from backend.online.pdf_finder import VERIFY_MAX_PAGES, pdf_verification, verify_pdf

    title = "Quantum dynamics of strongly correlated materials"
    path = _verification_pdf(tmp_path, ["Publisher cover", "Terms of access", title])
    assert verify_pdf(path, title, "10.1234/article")
    path = _verification_pdf(tmp_path, ["Publisher cover"] * VERIFY_MAX_PAGES + [title])
    assert pdf_verification(path, title, "10.1234/article")["status"] == "unverified"


def test_verification_distinguishes_contradictory_identity_from_insufficient_evidence(tmp_path):
    from backend.online.pdf_finder import pdf_verification, verify_pdf

    title = "Quantum dynamics of strongly correlated materials"
    path = _verification_pdf(tmp_path, ["A different study doi:10.1234/article-different"])
    result = pdf_verification(path, title, "10.1234/article")
    assert result["status"] == "wrong-paper" and result["reason"]
    assert not verify_pdf(path, title, "10.1234/article")  # identifier prefixes cannot prove identity
    path = _verification_pdf(tmp_path, ["Institutional access notice"])
    result = pdf_verification(path, title, "10.1234/article")
    assert result["status"] == "unverified" and result["reason"]
    path = _verification_pdf(tmp_path, [""])
    result = pdf_verification(path, title, "10.1234/article")
    assert result["status"] == "unverified" and "readable text" in result["reason"]
    assert not verify_pdf(path, title, "10.1234/article")


@pytest.mark.parametrize(
    "printed",
    [
        "doi:10.1234/long-\nidentifier.",
        "https://doi.org/10.1234/\nlong-identifier;",
        "doi:10.1234/long-identi\nfier)",
        "(doi:10.1234/long-identifier).",
    ],
)
def test_verification_matches_wrapped_dois_and_terminal_punctuation(tmp_path, printed):
    from backend.online.pdf_finder import verify_pdf

    path = _verification_pdf(tmp_path, [printed])
    assert verify_pdf(path, "The library title is absent from the PDF", "https://doi.org/10.1234/long-identifier")


def test_later_reference_does_not_override_a_different_opening_identifier(tmp_path):
    from backend.online.pdf_finder import pdf_verification

    path = _verification_pdf(tmp_path, ["doi:10.1234/different", "Paper body", "Reference doi:10.1234/expected"])
    assert pdf_verification(path, "The library title", "10.1234/expected")["status"] == "wrong-paper"


def test_verification_does_not_accept_a_prefix_of_an_arxiv_identifier(tmp_path):
    from backend.online.pdf_finder import verify_pdf

    path = _verification_pdf(tmp_path, ["arXiv:2403.051479"])
    assert not verify_pdf(path, None, None, "2403.05147")
