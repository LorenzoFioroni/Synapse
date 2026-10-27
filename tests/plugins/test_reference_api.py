"""Public reference sub-sources keep historical identities without rewriting saved citations."""

import pytest

from backend.plugins import PluginError, scholarly
from backend.plugins.references import ReferenceSource, clean_subsources, resolve, source_title
from backend.references.sources import FetchFailed


def run(pid, label, fn, query):
    return fn(query)


def provider(selected, names=None):
    return ReferenceSource(
        "scholarly",
        "Scholarly",
        lambda q: {"source": selected, "references": [{"title": "A cited work"}]},
        100,
        names or {},
    )


@pytest.mark.parametrize("name", ["semanticscholar", "openalex", "crossref", "arxiv-html"])
def test_bundled_names_keep_their_stored_and_http_identity(name):
    s = provider(name, {name: "A provider"})
    result, refs = resolve([("reference-sources", False, s)], run, {"item": {}})
    assert result == name and refs[0]["title"] == "A cited work"
    assert source_title(result, []) == source_title(f"reference-sources.scholarly.{name}", [])


def test_other_plugins_are_namespaced_and_cannot_claim_legacy_keys():
    s = provider("crossref", {"crossref": "My catalogue"})
    result, _ = resolve([("my-plugin", False, s)], run, {"item": {}})
    assert result == "my-plugin.scholarly.crossref"
    assert source_title(result, [("my-plugin", s)]) == "My catalogue"
    assert source_title("crossref", [("my-plugin", s)]) == "Crossref"
    assert source_title(result, []) == result


def test_existing_plugin_api_keeps_ignoring_undeclared_source_values():
    s = provider("crossref")
    assert resolve([("my-plugin", False, s)], run, {"item": {}})[0] == "my-plugin.scholarly"
    assert resolve([("old-core", True, s)], run, {"item": {}})[0] == "crossref"


@pytest.mark.parametrize("selected", [None, "not-declared", ["crossref"]])
def test_subsource_results_must_name_a_declared_provider(selected):
    with pytest.raises(FetchFailed, match="declared sub-sources"):
        resolve([("my-plugin", False, provider(selected, {"crossref": "Crossref"}))], run, {"item": {}})


@pytest.mark.parametrize("value", [[], {"Bad ID": "Title"}, {"one": ""}, {"one": 1}, {"one": "x" * 81}])
def test_invalid_declarations_are_rejected(value):
    with pytest.raises(PluginError, match="sub-sources"):
        clean_subsources(value)


def test_declarations_are_copied_and_reference_record_fields_are_public():
    original = {"arxiv-html": " arXiv HTML "}
    assert clean_subsources(original) == {"arxiv-html": "arXiv HTML"}
    assert original["arxiv-html"] == " arXiv HTML "
    assert scholarly.REF_FIELDS == (
        "doi",
        "arxiv_id",
        "openalex_id",
        "s2_id",
        "title",
        "authors",
        "year",
        "venue",
        "raw",
    )
    assert scholarly.identifiers({"doi": "https://doi.org/10.48550/arXiv.2101.00001v2"}) == (
        "10.48550/arxiv.2101.00001v2",
        "2101.00001",
    )
    assert scholarly.ARXIV_DOI.match("10.48550/arxiv.2101.00001")


def test_registered_subsource_labels_reach_the_reference_view(client, svc, make_plugin):
    make_plugin(
        "local-refs",
        """
        def activate(p):
            @p.reference_source("catalogues", title="Catalogues", subsources={"index": "My offline index"})
            def refs(q):
                return {"source": "index", "references": [{"title": "An indexed work"}]}
    """,
    )
    svc.plugins.enable("local-refs")
    paper = svc.papers.create({"doi": "10.1234/local", "title": "Local"})
    response = client.post(f"/api/items/{paper['id']}/references")
    assert response.status_code == 200
    assert response.json["fetch"]["source"] == "local-refs.catalogues.index"
    assert response.json["fetch"]["source_title"] == "My offline index"
    svc.plugins.disable("local-refs")
    response = client.get(f"/api/items/{paper['id']}/references")
    assert response.json["fetch"]["source"] == "local-refs.catalogues.index"
    assert len(response.json["references"]) == 1
    # Historical titles do not depend on whether a plugin is installed or running.
    svc.citations.store.save(paper["id"], "semanticscholar", [{"title": "Kept"}])
    assert client.get(f"/api/items/{paper['id']}/references").json["fetch"]["source_title"] == "Semantic Scholar"
