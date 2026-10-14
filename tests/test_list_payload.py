"""Lean item list and fetching a few items by id."""


def make(client, **kw):
    return client.post("/api/items", json={"title": "T", **kw}).get_json()


def test_lean_list_leaves_out_abstracts_and_empty_fields(client):
    a = make(client, title="With abstract", abstract="Long text " * 100, doi="10.1/x", year=2020)
    full = client.get("/api/items").get_json()["items"][0]
    lean = client.get("/api/items?lean=1").get_json()["items"][0]
    assert full["abstract"] and "abstract" not in lean
    assert lean["doi"] == "10.1/x" and lean["title"] == "With abstract" and lean["id"] == a["id"]
    assert all(v not in (None, "") for v in lean.values())
    # lists stay, even when empty: the list code reads .length on them
    assert lean["attachments"] == [] and lean["tags"] == [] and lean["folder_ids"] == []
    # everything else is the same as the full item
    assert {k: v for k, v in full.items() if k != "abstract" and v not in (None, "")} == lean


def test_lean_works_with_search_and_views(client):
    make(client, title="Alpha lindblad", abstract="x")
    make(client, title="Beta")
    res = client.get("/api/items?lean=1&q=lindblad").get_json()
    assert [i["title"] for i in res["items"]] == ["Alpha lindblad"] and "abstract" not in res["items"][0]
    assert res["counts"]["library"] == 2 and "sidebar" in res


def test_items_by_ids(client):
    a, b, c = make(client, title="A"), make(client, title="B"), make(client, title="C", abstract="abc")
    client.post("/api/items/trash", json={"ids": [b["id"]]})
    res = client.get(f"/api/items?ids={a['id']},{b['id']},{c['id']},nope").get_json()
    got = {i["id"]: i for i in res["items"]}
    assert set(got) == {a["id"], b["id"], c["id"]} and res["missing"] == ["nope"]
    assert got[b["id"]]["deleted_at"]  # the browser drops trashed ones from its list
    assert "abstract" not in got[c["id"]]
    assert res["counts"] == {"library": 2, "trash": 1, "duplicates": 0} and "folders" in res["sidebar"]


def test_ids_empty_gives_just_counts(client):
    make(client)
    res = client.get("/api/items?ids=").get_json()
    assert res["items"] == [] and res["counts"]["library"] == 1
