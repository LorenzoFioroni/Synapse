from backend import citekeys

PAPER = {"authors": "Cui, Jian; Cirac, J. Ignacio; Bañuls, Mari Carmen",
         "title": "Variational Matrix Product Operators for the Steady State", "year": 2015,
         "journal": "Physical Review Letters", "doi": "10.1103/PhysRevLett.114.220601"}


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
    assert citekeys.render("{auth}{year}", {"authors": "Mendoza-Arenas, Juan José", "year": "2019"}) == "mendozaarenas2019"


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
    assert seen == []                                    # first key is not a "change"
    svc.papers.update(item["id"], {"title": "Fresh"})    # no key change
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
