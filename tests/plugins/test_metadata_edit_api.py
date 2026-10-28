"""Public metadata edits preserve library validation, events and permission boundaries."""

import logging

import pytest

from backend.plugins import PluginError, scholarly
from backend.plugins.fields import LATER, FieldStore, declare
from backend.plugins.itemfields import FieldIndex, PluginFields


def test_edits_need_permission_and_a_running_plugin(client, svc, make_plugin):
    paper = svc.papers.create({"title": "Original"})
    make_plugin("reader", "def activate(p): pass", permissions={"library": ["read"]})
    make_plugin("editor", "def activate(p): pass", permissions={"library": ["edit"]})
    svc.plugins.enable("reader")
    svc.plugins.enable("editor")
    url = f"/api/plugin-host/{{}}/items/{paper['id']}"
    assert client.patch(url.format("reader"), json={"title": "Denied"}).status_code == 400
    assert svc.papers.get(paper["id"])["title"] == "Original"
    plugin = svc.plugins._active["editor"].plugin
    assert plugin.actions.update(paper["id"], {"title": "Backend edit"})["title"] == "Backend edit"
    assert client.patch(url.format("editor"), json={"title": "Frontend edit"}).json["title"] == "Frontend edit"
    svc.plugins.disable("editor")
    assert client.patch(url.format("editor"), json={"title": "Stopped"}).status_code == 404
    assert svc.papers.get(paper["id"])["title"] == "Frontend edit"


@pytest.mark.parametrize("invalid", [{"id": "other"}, {"tags": ["x"]}, {"folder_ids": []}, {"title": {"x": 1}}])
def test_invalid_metadata_rejects_the_entire_edit(client, svc, make_plugin, invalid):
    make_plugin("editor", "def activate(p): pass", permissions={"library": ["edit"]})
    svc.plugins.enable("editor")
    paper = svc.papers.create({"title": "Original"})
    response = client.patch(f"/api/plugin-host/editor/items/{paper['id']}", json={"title": "Changed", **invalid})
    assert response.status_code == 400
    assert svc.papers.get(paper["id"])["title"] == "Original"


def test_metadata_edit_uses_citation_key_and_item_events(client, svc, make_plugin):
    make_plugin("editor", "def activate(p): pass", permissions={"library": ["edit"]})
    svc.plugins.enable("editor")
    paper = svc.papers.create({"title": "Original", "citation_key": "oldkey"})
    events, keys = [], []
    svc.papers.on_items(lambda event, ids, **details: events.append((event, ids, details)))
    svc.papers.on_citekey_change(lambda *args: keys.append(args))
    response = client.patch(
        f"/api/plugin-host/editor/items/{paper['id']}", json={"title": "Updated", "citation_key": "newkey"}
    )
    assert response.status_code == 200
    assert keys == [(paper["id"], "oldkey", "newkey")]
    assert events == [("item.changed", [paper["id"]], {"fields": ["citation_key", "title"]})]
    assert client.patch("/api/plugin-host/editor/items/missing", json={"title": "x"}).status_code == 404


def test_due_snapshot_expiry_and_retry_without_waking(tmp_path):
    items = [{"id": "one", "title": "One"}, {"id": "two", "title": "Two"}]
    store = FieldStore(tmp_path / "fields.db")
    defs = []
    index = FieldIndex(tmp_path / "fields.db", lambda: defs)
    index._store = store
    fields = PluginFields("audit", "Audit", lambda: store, lambda ref: None, due=lambda d: index.due(d, items))
    d = fields.declare("audit", refresher=lambda papers: {p["key"]: LATER for p in papers}, refresh="1d")
    defs.append(d)
    fields.allow_retry = lambda name, keys: index.allow_retry("audit", name, keys)
    wakes = []
    fields.refresh_soon = lambda: wakes.append(True)
    index.refresh(d, items, logging.getLogger(__name__))
    assert fields.due("audit") == []  # deferred items respect backoff
    fields.refresh("audit", ["item:one"], wake=False)
    assert fields.due("audit") == [items[0]]
    assert wakes == []
    fields.refresh("audit")
    assert fields.due("audit") == items and wakes == [True]
    with pytest.raises(PluginError, match="declare"):
        fields.due("unknown")
    no_read = PluginFields("audit", "Audit", lambda: store, None)
    no_read.declare("audit", refresher=lambda papers: {})
    with pytest.raises(PluginError, match="read"):
        no_read.due("audit")


def test_stopped_refresher_cannot_write_late_values(tmp_path):
    defs = []

    def refresh(papers):
        defs.clear()  # the plugin was stopped while its lookup was in flight
        return {p["key"]: "Late" for p in papers}

    d = declare("audit", "Audit", "audit", refresher=refresh)
    defs.append(d)
    index = FieldIndex(tmp_path / "fields.db", lambda: defs)
    assert index.refresh(d, [{"id": "one", "title": "One"}], logging.getLogger(__name__)) == 0
    assert index.store.rows("audit", "audit") == {}


def test_metadata_diff_preserves_values_and_citation_key_opt_in():
    current = {"title": "Title", "citation_key": "stable"}
    fetched = {"title": "TITLE", "citation_key": "new", "abstract": "", "year": 2026}
    changes = scholarly.compute_changes(current, fetched)
    assert [c["key"] for c in changes] == ["title", "year", "citation_key"]
    assert changes[0]["checked"] and not changes[-1]["checked"]
    assert current["title"] == "Title" and fetched["abstract"] == ""
