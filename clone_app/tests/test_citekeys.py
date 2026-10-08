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
