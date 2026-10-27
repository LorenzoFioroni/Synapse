"""Reference and export providers: optional plugins with stable data/API contracts."""

import pytest

from backend import formats
from backend.online.http import LookupFailed
from backend.references.sources import fetch


@pytest.fixture(autouse=True)
def core_plugins(bundled):
    pass


def test_reference_sources_off_keeps_saved_graph_and_stops_network(client, svc, monkeypatch):
    paper = svc.papers.create({"title": "Kept", "doi": "10.1234/kept"})
    svc.citations.store.save(paper["id"], "crossref", [{"doi": "10.1234/cited", "title": "Cited"}])
    assert svc.plugins.info("reference-sources")["reference_sources"]
    svc.plugins.disable("reference-sources")
    monkeypatch.setattr(
        "requests.request", lambda *a, **k: pytest.fail("disabled reference providers used the network")
    )
    result = client.get(f"/api/items/{paper['id']}/references").json
    assert result["fetch"]["source"] == "crossref" and len(result["references"]) == 1
    result = client.post(f"/api/items/{paper['id']}/references")
    assert result.status_code == 404 and "No reference sources" in result.json["error"]
    assert len(client.get(f"/api/items/{paper['id']}/references").json["references"]) == 1
    svc.plugins.enable("reference-sources")
    assert svc.plugins.info("reference-sources")["running"]


def test_custom_reference_provider_order_errors_and_unload(svc, make_plugin, monkeypatch):
    svc.plugins.disable("reference-sources")
    make_plugin(
        "custom-refs",
        """
        from backend.plugins import PluginError
        def activate(p):
            @p.reference_source("empty", title="Empty", order=1)
            def empty(q): return None
            @p.reference_source("down", title="Unavailable", order=2)
            def down(q): raise PluginError("offline")
            @p.reference_source("local", title="Offline index", order=3)
            def local(q):
                assert q["fresh"] and q["item"]["doi"] == "10.1234/paper"
                return {"references": [{"doi": "10.1234/cited", "title": "Found"}]}
    """,
    )
    svc.plugins.enable("custom-refs")
    monkeypatch.setattr("requests.get", lambda *a, **k: pytest.fail("custom provider used the network"))
    name, refs = fetch({"doi": "10.1234/paper"}, fresh=True, sources=svc.plugins.fetch_references)
    assert name == "custom-refs.local" and refs[0]["title"] == "Found"
    svc.plugins.disable("custom-refs")
    with pytest.raises(LookupFailed, match="No reference sources"):
        fetch({"doi": "10.1234/paper"}, sources=svc.plugins.fetch_references)


@pytest.mark.parametrize("fmt", formats.FORMATS)
def test_export_plugin_preserves_existing_output_and_imports(client, svc, fmt):
    paper = svc.papers.create({"title": "A paper", "authors": "Doe, Jane", "year": 2026, "doi": "10.1234/a"})
    result = client.post("/api/export", json={"format": fmt, "ids": [paper["id"]], "download": False})
    assert result.status_code == 200
    items = [dict(svc.papers.get(paper["id"]), keywords="")]
    expected = formats.dump(
        items,
        fmt,
        unicode=svc.settings.get("bibtex_unicode"),
        abstract=svc.settings.get("export_abstract"),
        pattern=svc.settings.get("citekey_pattern"),
    )
    assert result.json["text"] == expected
    # the plugin's own key gives the same text; the old key above is an alias for it
    own = client.post("/api/export", json={"format": f"export-formats.{fmt}", "ids": [paper["id"]], "download": False})
    assert own.json["text"] == expected
    svc.plugins.disable("export-formats")
    assert client.get("/api/formats").json["exports"] == []
    assert client.get("/api/formats").json["formats"] == formats.listing()
    assert client.post("/api/export", json={"format": fmt}).status_code == 400
    if fmt == "bibtex":
        copied = client.post("/api/clipboard/bibtex", json={"ids": [paper["id"]]})
        assert copied.status_code == 200 and copied.json["text"] == expected
        assert client.post("/api/clipboard/bibtex", json={"ids": []}).json["count"] == 0
        assert client.post("/api/clipboard/bibtex", json={"ids": "invalid"}).status_code == 400
    assert formats.parse(expected, fmt=fmt)[1][0]["title"] == "A paper"
    svc.plugins.enable("export-formats")
    assert client.post("/api/export", json={"format": fmt, "download": False}).status_code == 200


def test_third_party_export_format_registration_and_disposal(client, svc, make_plugin):
    make_plugin(
        "plain-export",
        """
        def activate(p):
            @p.export_format("titles", title="Titles", extension="txt", mimetype="text/plain")
            def titles(items, options):
                return "\\n".join(item["title"] for item in items)
    """,
    )
    svc.plugins.enable("plain-export")
    svc.papers.create({"title": "Exported"})
    assert any(f["key"] == "plain-export.titles" for f in client.get("/api/formats").json["exports"])
    response = client.post("/api/export", json={"format": "plain-export.titles", "download": False})
    assert response.json["text"] == "Exported"
    svc.plugins.disable("plain-export")
    assert all(f["key"] != "plain-export.titles" for f in client.get("/api/formats").json["exports"])


def test_export_formats_is_a_contributed_plugin(client, svc):
    """It starts on by itself, names its formats like any plugin, and writes through the public
    backend.plugins.formats."""
    from backend.plugins import formats as public

    info = svc.plugins.info("export-formats")
    assert info["enabled"] and not info["core"]
    keys = [f["key"] for f in client.get("/api/formats").json["exports"]]
    assert (
        keys
        == [f"export-formats.{w.key}" for w in public.writers()]
        == [f"export-formats.{k}" for k in formats.FORMATS]
    )
    assert svc.settings.get("export_format") == "export-formats.bibtex"
    item = {"title": "T", "authors": "Doe, Jane", "year": 2026, "item_type": "journalArticle"}
    assert public.dump([item], "ris", abstract=True) == formats.dump([item], "ris", abstract=True)
    with pytest.raises(Exception, match="Unknown reference format"):
        public.dump([item], "docx")
