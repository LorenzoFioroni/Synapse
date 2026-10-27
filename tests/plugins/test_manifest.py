"""Reading and checking manifest.json."""

from pathlib import Path

import pytest

from backend.plugins.manifest import API_VERSION, ManifestError, parse, read
from tests.plugins.conftest import EXAMPLE


def man(tmp_path: Path, pid: str = "demo", **data):
    folder = tmp_path / pid
    folder.mkdir(exist_ok=True)
    return parse({"id": pid, "name": "Demo", "version": "1.0", "api": "^1.0", **data}, folder)


def test_the_example_manifest_is_valid():
    m = read(EXAMPLE)
    assert m.id == "recently-added"
    assert m.api == (1, 0) and m.compatible() is None
    assert m.permissions() == {"network": [], "library": ["read"], "notes": [], "tasks": [], "mcp": True, "python": []}
    assert m.defaults() == {"keep_days": 90, "skip_tags": []}
    assert m.backend_path() == (EXAMPLE / "backend").resolve()


@pytest.mark.parametrize(
    "data, message",
    [
        ({"id": "Bad Id"}, '"id"'),
        ({"id": "other"}, "folder is called"),
        ({"api": "one"}, '"api"'),
        ({"name": ""}, '"name" is missing'),
        ({"permissions": {"camera": True}}, "unknown permission 'camera'"),
        ({"permissions": {"network": ["https://x.org"]}}, "host names"),
        ({"permissions": {"library": ["write"]}}, '"library"'),
        ({"backend": "missing.py"}, "does not exist"),
        ({"backend": "../outside.py"}, "outside the plugin"),
        ({"settings": [{"key": "Days", "type": "int"}]}, "lowercase"),
        ({"settings": [{"key": "days", "type": "date"}]}, "type is one of"),
        ({"settings": [{"key": "days", "type": "int", "default": 0, "min": 1}]}, "default does not fit"),
        ({"settings": [{"key": "kind", "type": "choice"}]}, '"options"'),
        ({"settings": [{"key": "a", "type": "bool"}, {"key": "a", "type": "bool"}]}, "appears twice"),
    ],
)
def test_bad_manifests_say_what_to_fix(tmp_path, data, message):
    (tmp_path / "outside.py").write_text("")
    with pytest.raises(ManifestError, match=message):
        man(tmp_path, **{"id": "demo", **data})


def test_a_missing_or_broken_manifest(tmp_path):
    (tmp_path / "demo").mkdir()
    with pytest.raises(ManifestError, match="missing"):
        read(tmp_path / "demo")
    (tmp_path / "demo" / "manifest.json").write_text("{nope")
    with pytest.raises(ManifestError, match="cannot be read"):
        read(tmp_path / "demo")


def test_api_versions(tmp_path):
    major, minor = API_VERSION
    assert man(tmp_path, api=f"{major}").compatible() is None
    assert man(tmp_path, api=f"^{major}.{minor}").compatible() is None
    assert "needs plugin API" in man(tmp_path, api=f"{major}.{minor + 1}").compatible()
    assert "needs plugin API" in man(tmp_path, api=f"{major + 1}.0").compatible()


def test_settings_clean_values(tmp_path):
    m = man(
        tmp_path,
        settings=[
            {"key": "on", "type": "bool"},
            {"key": "days", "type": "int", "default": 7, "min": 1, "max": 30},
            {"key": "ratio", "type": "float", "default": 0.5},
            {"key": "kind", "type": "choice", "options": ["a", ["b", "Bee"]]},
            {"key": "cats", "type": "multi", "options": ["x", "y"], "default": ["x"]},
            {"key": "words", "type": "multi"},
            {"key": "note", "type": "text"},
        ],
    )
    s = {x.key: x for x in m.settings}
    assert m.defaults() == {"on": False, "days": 7, "ratio": 0.5, "kind": "a", "cats": ["x"], "words": [], "note": ""}
    assert s["on"].clean(True) is True
    with pytest.raises(ManifestError):
        s["on"].clean("yes")
    assert s["days"].clean("12") == 12
    for bad in (0, 31, 2.5, "x", True):
        with pytest.raises(ManifestError):
            s["days"].clean(bad)
    assert s["ratio"].clean(1) == 1.0
    assert s["kind"].clean("b") == "b"
    with pytest.raises(ManifestError, match="one of a, b"):
        s["kind"].clean("c")
    assert s["cats"].clean(["y", "x", "y"]) == ["y", "x"]
    with pytest.raises(ManifestError, match="not one of"):
        s["cats"].clean(["z"])
    assert s["words"].clean("quant-ph, cond-mat , ") == ["quant-ph", "cond-mat"]
    assert s["kind"].describe()["options"] == [["a", "a"], ["b", "Bee"]]
