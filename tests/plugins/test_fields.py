"""Per-item fields: keys, types and filters; plugins setting values and Synapse asking a refresher;
values in the list, the item pane, the search, Claude's tools, the change feed and backups."""

import json
import tarfile
import time

import pytest

from backend.mcp import build_server
from backend.plugins.errors import PluginError
from backend.plugins.fields import FieldStore, declare, key_of, keys_of, matches

FIELDS = """
from backend.plugins import HttpError, Plugin

def activate(plugin: Plugin):
    asked = plugin.storage.json("asked")

    def score(papers):
        asked.set(str(len(asked.all())), [p["key"] for p in papers])
        if plugin.settings.get("offline"):
            raise HttpError("no network")
        return {p["key"]: (len(p["title"]), "https://example.org/" + p["key"]) for p in papers if p["title"] != "Unknown"}

    plugin.fields.declare("score", type="int", label="Score", source="Example", refresher=score, refresh="1d")
    plugin.fields.declare("mood", type="enum", options=["good", "bad"])
    plugin.fields.declare("seen", type="bool")

    @plugin.route("/set", methods=["POST"])
    def set_value():
        from flask import request
        b = request.get_json()
        plugin.fields.set(b["ref"], b["name"], b["value"])
        return {"get": plugin.fields.get(b["ref"], b["name"])}
"""


def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def plugin(client, svc, make_plugin):
    make_plugin(
        "scores",
        FIELDS,
        permissions={"library": ["read"]},
        settings=[{"key": "offline", "type": "bool", "default": False}],
    )
    assert client.post("/api/plugin-host/scores/enable").get_json()["plugin"]["running"]
    return svc.plugins._active["scores"].plugin


def set_value(client, ref, name, value):
    r = client.post("/api/plugins/scores/set", json={"ref": ref, "name": name, "value": value})
    return r


# ------------------------------------------------------------------ rules
def test_keys():
    item = {
        "id": "abc",
        "title": "T",
        "doi": "https://doi.org/10.1103/PhysRevLett.1",
        "url": "https://arxiv.org/abs/2610.01234v2",
    }
    assert keys_of(item) == ["doi:10.1103/physrevlett.1", "arxiv:2610.01234", "item:abc"]
    assert keys_of({"id": "x", "doi": "10.48550/arXiv.2101.00001"}) == ["arxiv:2101.00001", "item:x"]
    assert key_of({"arxiv": "arXiv:2610.01234v3"}) == "arxiv:2610.01234"
    assert key_of({"doi": "DOI:10.1/AB"}) == "doi:10.1/ab"
    assert key_of("doi:10.1/AB") == "doi:10.1/ab"
    assert key_of({"key": "arxiv:2610.01234"}) == "arxiv:2610.01234"
    assert key_of("abc", lambda i: {"id": i, "title": "T"} if i == "abc" else None) == "item:abc"
    with pytest.raises(PluginError):
        key_of("nothing", lambda i: None)


def test_types_are_checked():
    d = declare("p", "P", "n", type="int")
    assert d.clean(3) == 3 and d.clean("4") == 4 and d.clean(None) is None
    for bad in (2.5, True, "x"):
        with pytest.raises(PluginError):
            d.clean(bad)
    assert declare("p", "P", "d", type="date").clean("2026-10-22T10:00:00Z") == "2026-10-22"
    assert declare("p", "P", "b", type="bool").clean(False) is False
    with pytest.raises(PluginError):
        declare("p", "P", "u", type="url").clean("ftp://x")
    with pytest.raises(PluginError, match="needs options"):
        declare("p", "P", "e", type="enum")
    with pytest.raises(PluginError):
        declare("p", "P", "Bad Name")
    with pytest.raises(PluginError):
        declare("p", "P", "x", type="color")


def test_filters():
    n = declare("p", "P", "n", type="int")
    row = lambda v: {"value": v}  # noqa: E731
    assert matches(n, row(150), ">100") and not matches(n, row(50), ">100")
    assert matches(n, row(10), "5-20") and matches(n, row(7), "7") and matches(n, row(7), "<=7")
    assert matches(n, None, "none") and matches(n, row(None), "none") and not matches(n, row(0), "none")
    assert matches(n, row(0), "any") and not matches(n, None, "any")
    assert matches(n, row(0), "0")  # 0 is a value
    d = declare("p", "P", "d", type="date")
    assert matches(d, row("2026-10-22"), ">2026-10-01") and matches(d, row("2026-10-22"), "2026-10")
    t = declare("p", "P", "t", type="text")
    assert matches(t, row("Bañuls group"), "banuls")
    b = declare("p", "P", "b", type="bool")
    assert matches(b, row(True), "yes") and matches(b, row(False), "false") and not matches(b, row(False), "true")


def test_store_keeps_none_apart_from_missing(tmp_path):
    st = FieldStore(tmp_path / "f.db")
    st.put("p", "n", {"doi:1": (None, None), "doi:2": (0, "https://x")})
    rows = st.rows("p", "n")
    assert rows["doi:1"]["value"] is None and rows["doi:2"]["value"] == 0 and "doi:3" not in rows
    assert st.delete("p", "n", ["doi:1"]) == 1 and "doi:1" not in st.rows("p", "n")


# ------------------------------------------------------------------ in the app
def test_values_reach_the_list_the_item_and_the_pane(client, svc, plugin):
    a = client.post("/api/items", json={"title": "Alpha", "doi": "10.1/a"}).get_json()
    b = client.post("/api/items", json={"title": "Beta"}).get_json()
    assert set_value(client, a["id"], "mood", "good").get_json() == {"get": "good"}
    assert set_value(client, {"doi": "10.1/A"}, "seen", True).status_code == 200  # by DOI: the same paper
    assert set_value(client, b["id"], "mood", "meh").status_code == 400  # not an option
    fields = client.get("/api/plugin-fields").get_json()["fields"]
    assert [f["id"] for f in fields] == ["scores.score", "scores.mood", "scores.seen"]
    items = {i["id"]: i for i in client.get("/api/items?lean=1").get_json()["items"]}
    f = items[a["id"]]["_f"]
    assert f["scores.mood"] == "good" and f["scores.seen"] is True  # (a score may be there too, from the refresher)
    assert client.get(f"/api/items/{a['id']}").get_json()["_f"]["scores.mood"] == "good"
    assert client.get(f"/api/items?ids={a['id']}").get_json()["items"][0]["_f"]["scores.seen"] is True
    pane = {f["name"]: f for f in client.get(f"/api/items/{a['id']}/fields").get_json()["fields"]}
    assert pane["mood"]["value"] == "good" and pane["mood"]["checked"] and pane["mood"]["updated"]
    assert pane["mood"]["plugin_name"] == "Scores"


def test_the_refresher_fills_values_in_the_background(client, svc, plugin):
    client.post("/api/items", json={"title": "Alpha", "doi": "10.1/a"})
    u = client.post("/api/items", json={"title": "Unknown"}).get_json()
    store = svc.plugins.fields.store
    assert wait(lambda: {"doi:10.1/a", f"item:{u['id']}"} <= set(store.rows("scores", "score")))
    rows = store.rows("scores", "score")
    assert rows["doi:10.1/a"]["value"] == 5 and rows["doi:10.1/a"]["source_url"] == "https://example.org/doi:10.1/a"
    assert rows[f"item:{u['id']}"]["value"] is None  # looked, nothing found: not asked again at once
    pane = {f["name"]: f for f in client.get(f"/api/items/{u['id']}/fields").get_json()["fields"]}
    assert pane["score"]["checked"] and pane["score"]["value"] is None
    asked = plugin.storage.json("asked").all()
    n = len(asked)
    plugin.wake()
    time.sleep(0.3)
    assert len(plugin.storage.json("asked").all()) == n  # nothing due
    assert svc.plugins.fields.due(plugin.fields.defs["score"], svc.papers.list(), now=time.time() + 2 * 86400)


def test_an_unreachable_web_leaves_values_unset(client, svc, plugin):
    client.put("/api/plugin-host/scores/settings", json={"offline": True})
    time.sleep(0.2)
    a = client.post("/api/items", json={"title": "Alpha", "doi": "10.1/off"}).get_json()
    assert wait(lambda: any("doi:10.1/off" in v for v in plugin.storage.json("asked").all().values()))
    time.sleep(0.2)
    assert "doi:10.1/off" not in svc.plugins.fields.store.rows("scores", "score")
    assert svc.plugins.info("scores")["running"]  # not a fault
    assert a


def test_search_filters(client, svc, plugin):
    a = client.post("/api/items", json={"title": "Alpha", "doi": "10.1/a"}).get_json()
    b = client.post("/api/items", json={"title": "Betabeta"}).get_json()
    client.post("/api/items", json={"title": "Gamma"}).get_json()
    set_value(client, a["id"], "mood", "good")
    set_value(client, b["id"], "mood", "bad")
    titles = lambda q: sorted(i["title"] for i in client.get(f"/api/items?q={q}").get_json()["items"])  # noqa: E731
    assert titles("mood:good") == ["Alpha"]
    assert titles("scores.mood:bad") == ["Betabeta"]
    assert titles("-mood:good") == ["Betabeta", "Gamma"]
    assert titles("mood:none") == ["Gamma"]
    assert wait(lambda: len(svc.plugins.fields.store.rows("scores", "score")) == 3)
    assert titles("score:>5") == ["Betabeta"]
    assert titles("arXiv:2205.03415") == []  # still plain text


def test_outside_papers_keep_their_values_when_added(client, svc, plugin):
    plugin.fields.set({"arxiv": "2610.01234"}, "mood", "good")
    item = client.post("/api/items", json={"title": "Later", "url": "https://arxiv.org/abs/2610.01234"}).get_json()
    assert client.get(f"/api/items/{item['id']}").get_json()["_f"]["scores.mood"] == "good"


def test_claude_sees_fields(client, svc, plugin):
    a = client.post("/api/items", json={"title": "Alpha", "citation_key": "alpha2026"}).get_json()
    set_value(client, a["id"], "mood", "good")
    server = build_server(svc)
    res = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_paper", "arguments": {"paper": "alpha2026"}},
        }
    )["result"]
    assert json.loads(res["content"][0]["text"])["plugin_fields"]["Mood (Scores)"] == "good"
    res = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "search_library", "arguments": {"query": "mood:good", "scope": "items"}},
        }
    )["result"]
    assert [i["key"] for i in json.loads(res["content"][0]["text"])["items"]] == ["alpha2026"]


def test_changes_are_announced(client, svc, plugin):
    a = client.post("/api/items", json={"title": "Alpha"}).get_json()
    since = svc.changes.seq
    set_value(client, a["id"], "mood", "good")
    assert "fields" in svc.changes.since(since, svc.changes.epoch, None)["changes"]


def test_turned_off_the_fields_go_and_come_back(client, svc, plugin):
    a = client.post("/api/items", json={"title": "Alpha"}).get_json()
    set_value(client, a["id"], "mood", "good")
    client.post("/api/plugin-host/scores/disable")
    assert client.get("/api/plugin-fields").get_json()["fields"] == []
    assert "_f" not in client.get(f"/api/items/{a['id']}").get_json()
    assert client.get("/api/items?q=mood:good").get_json()["items"] == []  # plain text now
    client.post("/api/plugin-host/scores/enable")
    assert client.get(f"/api/items/{a['id']}").get_json()["_f"]["scores.mood"] == "good"


def test_fields_are_in_backups(client, svc, plugin):
    a = client.post("/api/items", json={"title": "Alpha"}).get_json()
    set_value(client, a["id"], "mood", "good")
    entry = svc.backups.create("manual")
    with tarfile.open(svc.backups.dir / entry["name"]) as tar:
        assert "fields.db" in tar.getnames()
    set_value(client, a["id"], "mood", "bad")
    client.post("/api/backups/restore", json={"name": entry["name"]})
    assert client.get(f"/api/items/{a['id']}").get_json()["_f"]["scores.mood"] == "good"


def test_the_example_declares_two_fields(client, svc, example):
    client.post("/api/plugin-host/recently-added/enable")
    item = client.post("/api/items", json={"title": "New", "authors": "A, B; C, D"}).get_json()
    # The item-added listener and computed-field refresher run on independent workers.
    assert svc.plugins.drain("recently-added"), "item-added listener did not finish"
    assert wait(
        lambda: (
            (client.get(f"/api/items/{item['id']}").get_json().get("_f") or {}).get("recently-added.author_count") == 2
        )
    )
    f = client.get(f"/api/items/{item['id']}").get_json()["_f"]
    assert f["recently-added.added_on"] == time.strftime("%Y-%m-%d", time.gmtime())
