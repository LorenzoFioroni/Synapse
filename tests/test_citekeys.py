from backend.library import citekeys

PAPER = {
    "authors": "Cui, Jian; Cirac, J. Ignacio; Bañuls, Mari Carmen",
    "title": "Variational Matrix Product Operators for the Steady State",
    "year": 2015,
    "journal": "Physical Review Letters",
    "doi": "10.1103/PhysRevLett.114.220601",
}


def test_default_pattern_matches_old_behaviour():
    assert citekeys.render("{auth}{Title}{year}", PAPER) == "cuiVariational2015"


def test_tokens():
    assert citekeys.render("{Auth}_{yy}", PAPER) == "Cui_15"
    assert citekeys.render("{AuthEtAl}{year}", PAPER) == "CuiEtAl2015"
    assert citekeys.render("{authEtAl}", {"authors": "Cui, J; Cirac, I"}) == "cuiCirac"
    assert citekeys.render("{ShortTitle}", PAPER) == "VariationalMatrixProduct"
    assert citekeys.render("{journal}", PAPER) == "prl"
    assert citekeys.render("{AUTH}", {"authors": "Žnidarič, Marko"}) == "ZNIDARIC"


def test_accents_and_particles_are_ascii():
    assert (
        citekeys.render("{auth}{year}", {"authors": "Mendoza-Arenas, Juan José", "year": "2019"}) == "mendozaarenas2019"
    )


def test_unique_suffixes():
    taken = {"cui2015", "cui2015a"}
    assert citekeys.make_unique("cui2015", taken) == "cui2015b"
    assert citekeys.make_unique("new", taken) == "new"


def test_required_fields():
    assert citekeys.required_fields("{auth}{Title}{year}") == {"authors", "title", "year"}
    assert citekeys.required_fields("{AuthEtAl}_{yy}") == {"authors", "year"}


# --- stability: existing keys never change unless the user explicitly asks -------------------


def test_metadata_edits_never_change_an_existing_key(svc):
    item = svc.papers.create({"authors": "Cui, Jian", "title": "Variational", "year": 2015})
    key = item["citation_key"]
    assert key
    after = svc.papers.update(item["id"], {"authors": "Smith, Anna", "title": "Other title", "year": 1999})
    assert after["citation_key"] == key


def test_items_without_key_get_one_once_complete(svc):
    item = svc.papers.create({"title": ""}, auto_citekey=True)
    assert not item.get("citation_key")
    after = svc.papers.update(item["id"], {"authors": "Cui, Jian", "title": "Variational", "year": 2015})
    assert after["citation_key"] == "cuiVariational2015"


def test_key_changes_are_reported_exactly_once(svc):
    seen = []
    svc.papers.on_citekey_change(lambda pid, old, new: seen.append((pid, old, new)))
    item = svc.papers.create({"authors": "Cui, Jian", "title": "Variational", "year": 2015})
    assert seen == []  # first key is not a "change"
    svc.papers.update(item["id"], {"title": "Fresh"})  # no key change
    svc.papers.update(item["id"], {"citation_key": item["citation_key"]})  # same key
    assert seen == []
    svc.papers.update(item["id"], {"citation_key": "renamed2015"})
    assert seen == [(item["id"], "cuiVariational2015", "renamed2015")]
    svc.papers.update(item["id"], {"authors": "Smith, A"})
    svc.papers.regenerate_citekeys([item["id"]])
    assert seen[-1] == (item["id"], "renamed2015", "smithFresh2015")
    assert len(seen) == 2


def test_failing_listener_does_not_break_update(svc):
    def boom(*a):
        raise RuntimeError("x")

    svc.papers.on_citekey_change(boom)
    item = svc.papers.create({"authors": "Cui, Jian", "title": "Variational", "year": 2015})
    assert svc.papers.update(item["id"], {"citation_key": "k2"})["citation_key"] == "k2"


# --- validation: every writer uses the same repository rules -------------------------------


def test_safe_key_characters_and_empty_keys(svc):
    for key in ("AZaz09_-:./+x", "a+", "_a", "9", "", None):
        item = svc.papers.create({"citation_key": key}, auto_citekey=False)
        assert item["citation_key"] == key


def test_generated_keys_use_the_safe_set(svc):
    svc.settings.update({"citekey_pattern": "{auth}+/{year}"})
    item = svc.papers.create({"authors": "Cui, Jian", "year": 2015}, auto_citekey=True)
    assert item["citation_key"] == "cui+/2015"
    # an empty token must not leave punctuation that citations would cut off
    svc.settings.update({"citekey_pattern": "{journal}:{auth}{year}:{doi}"})
    item = svc.papers.create({"authors": "Cui, Jian", "year": 2016}, auto_citekey=True)
    assert item["citation_key"] == "cui2016"


def test_invalid_keys_are_rejected_by_create_and_update(svc):
    import pytest

    from backend.library.citekeys import CitationKeyError

    item = svc.papers.create({"citation_key": "valid"})
    seen = []
    svc.papers.on_citekey_change(lambda *args: seen.append(args))
    for key in (
        *("has space", "a,b", "{x}", 'a"b', "a'b", "a;b", "a\\b", "a\nb", "a\tb", "café", "<img src=x>"),
        # what the notes' citation parser cannot read as one key
        *("-lead", ".lead", "/lead", ":lead", "+lead", "trail.", "trail:", "trail/", "trail-"),
    ):
        with pytest.raises(CitationKeyError):
            svc.papers.create({"citation_key": key})
        with pytest.raises(CitationKeyError):
            svc.papers.update(item["id"], {"citation_key": key, "title": "Must not save"})
    assert svc.papers.get(item["id"])["citation_key"] == "valid"
    assert not svc.papers.get(item["id"])["title"]
    assert svc.papers.counts()["library"] == 1
    assert seen == []


def test_duplicate_keys_include_trash_and_exclude_the_item_itself(svc):
    import pytest

    from backend.library.citekeys import CitationKeyError

    first = svc.papers.create({"title": "First paper", "citation_key": "same"})
    second = svc.papers.create({"citation_key": "other"})
    assert svc.papers.update(first["id"], {"citation_key": "same"})["citation_key"] == "same"
    svc.papers.trash([first["id"]])
    with pytest.raises(CitationKeyError, match="already used by.*First paper"):
        svc.papers.create({"citation_key": "same"}, auto_citekey=False)
    with pytest.raises(CitationKeyError):
        svc.papers.update(second["id"], {"citation_key": "same"})
    with pytest.raises(CitationKeyError):  # differs only in case: the paper-note files would collide
        svc.papers.create({"citation_key": "Same"})
    assert svc.papers.update(first["id"], {"citation_key": "SAME"})["citation_key"] == "SAME"
    assert svc.papers.update(second["id"], {"citation_key": ""})["citation_key"] == ""


def test_batch_validation_is_atomic_and_reserves_explicit_keys(svc):
    import pytest

    from backend.library.citekeys import CitationKeyError

    for entries in (
        [{"citation_key": "fresh"}, {"citation_key": "bad key"}],
        [{"citation_key": "same"}, {"citation_key": "same"}],
    ):
        with pytest.raises(CitationKeyError):
            svc.papers.create_many(entries, auto_citekey=False)
        assert svc.papers.list() == []
    svc.settings.update({"citekey_pattern": "{auth}{year}"})
    ids = svc.papers.create_many(
        [
            {"authors": "Cui, Jian", "year": 2015},
            {"citation_key": "cui2015"},
            {"authors": "Cui, Jian", "year": 2015},
        ],
        auto_citekey=True,
    )
    assert [svc.papers.get(pid)["citation_key"] for pid in ids] == ["cui2015a", "cui2015", "cui2015b"]


def test_existing_legacy_keys_survive_metadata_edits(svc):
    item = svc.papers.create({"citation_key": "valid"})
    with svc.papers.db.session() as c:
        c.execute("UPDATE papers SET citation_key = ? WHERE id = ?", ("legacy key", item["id"]))
    assert svc.papers.update(item["id"], {"title": "New title"})["citation_key"] == "legacy key"
    assert svc.papers.update(item["id"], {"citation_key": "legacy key"})["citation_key"] == "legacy key"


def test_concurrent_writers_cannot_claim_the_same_key(svc):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from backend.library.citekeys import CitationKeyError

    barrier = Barrier(2)

    def create(_):
        barrier.wait()
        try:
            return svc.papers.create({"citation_key": "shared"})["citation_key"]
        except CitationKeyError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == ["rejected", "shared"]
    assert len(svc.papers.list()) == 1


def test_http_validation_returns_actionable_400s(client):
    first = client.post("/api/items", json={"title": "Original", "citation_key": "same"}).get_json()
    second = client.post("/api/items", json={"citation_key": "other"}).get_json()
    for key in ("same", "bad key"):
        created = client.post("/api/items", json={"citation_key": key})
        updated = client.patch(f"/api/items/{second['id']}", json={"citation_key": key})
        assert created.status_code == updated.status_code == 400
        assert created.get_json()["error"] == updated.get_json()["error"]
    assert first["id"] in client.post("/api/items", json={"citation_key": "same"}).get_json()["error"]
    # an import keeps going: a key that is taken gets a free one, and the response says so
    imported = client.post(
        "/api/import/commit",
        json={"entries": [{"citation_key": "fresh"}, {"title": "Again", "citation_key": "same"}]},
    )
    assert imported.status_code == 200
    body = imported.get_json()
    assert len(body["created"]) == 2
    assert body["renamed_keys"] == [{"id": body["created"][1], "title": "Again", "from": "same", "to": "samea"}]
    assert len(client.get("/api/items").get_json()["items"]) == 4


def test_imports_replace_keys_they_cannot_keep(svc):
    svc.settings.update({"citekey_auto": False, "citekey_pattern": "{auth}{year}"})
    svc.papers.create({"title": "Already here", "citation_key": "smith2020"})
    entries = [
        {"title": "Plus", "citation_key": "Smith+2021"},  # fine as it is
        {"title": "Apostrophe", "citation_key": "OBrien'21"},
        {"title": "Accent", "citation_key": "Müller2021"},
        {"title": "Taken", "citation_key": "smith2020"},
        {"title": "Taken but for case", "citation_key": "Smith2020"},
        {"title": "Twice in the file", "citation_key": "dup"},
        {"title": "Twice in the file again", "citation_key": "dup"},
        {"title": "Ends badly", "citation_key": "-odd."},
        {"title": "Nothing usable", "authors": "Cui, Jian", "year": 2015, "citation_key": "{}"},
        {"title": "Nothing usable, no metadata", "citation_key": "{}"},
        {"title": "No key"},
    ]
    ids, changes = svc.papers.import_many(entries)
    keys = [svc.papers.get(i)["citation_key"] or "" for i in ids]
    assert keys == [
        "Smith+2021",
        "OBrien21",
        "Muller2021",
        "smith2020a",
        "Smith2020b",
        "dup",
        "dupa",
        "odd",
        "cui2015",
        "item",  # as the pattern makes it for an item without author or year
        "",
    ]
    assert [(c["from"], c["to"]) for c in changes] == [
        ("OBrien'21", "OBrien21"),
        ("Müller2021", "Muller2021"),
        ("smith2020", "smith2020a"),
        ("Smith2020", "Smith2020b"),
        ("dup", "dupa"),
        ("-odd.", "odd"),
        ("{}", "cui2015"),
        ("{}", "item"),
    ]
    assert all(c["id"] in ids for c in changes)


def test_sanitize_gives_keys_that_validate():
    import random

    rnd = random.Random(7)
    alphabet = "aZ09_-:./+ 'é{}ß,;\\"
    for _ in range(2000):
        raw = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 12)))
        citekeys.validate(citekeys.sanitize(raw))  # never raises
        citekeys.validate(citekeys.render(raw + "{title}" + raw, {"title": "Word"}))
