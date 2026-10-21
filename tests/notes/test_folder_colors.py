"""Colours of notes folders (backend/notes/folder_colors.py): given and taken back, moved with their
folder, kept out of the listing for folders that are gone, refused for bad values."""

import json

import pytest

from backend.notes.folder_colors import FolderColorError, FolderColors


def test_set_and_clear(tmp_path):
    fc = FolderColors(tmp_path)
    assert fc.all() == {}
    assert fc.set("Projects/", "#0090FF") == {"Projects": "#0090ff"}
    assert json.loads((tmp_path / ".folders.json").read_text()) == {"Projects": "#0090ff"}
    assert fc.set("Projects", None) == {}
    assert fc.set("Other", "#e5484d") == {"Other": "#e5484d"}
    assert fc.set("Other", "") == {}


@pytest.mark.parametrize("bad", ["blue", "#09f", "#0090ffff", "url(x)", "#gggggg"])
def test_bad_colours_are_refused(tmp_path, bad):
    with pytest.raises(FolderColorError):
        FolderColors(tmp_path).set("A", bad)


def test_a_folder_is_needed(tmp_path):
    with pytest.raises(FolderColorError):
        FolderColors(tmp_path).set("/", "#0090ff")


def test_moving_a_folder_moves_its_colours(tmp_path):
    fc = FolderColors(tmp_path)
    fc.set("A", "#0090ff")
    fc.set("A/Sub", "#30a46c")
    fc.set("Ab", "#e5484d")  # not inside A
    fc.moved("A", "B/A2")
    assert fc.all() == {"B/A2": "#0090ff", "B/A2/Sub": "#30a46c", "Ab": "#e5484d"}


def test_a_damaged_file_means_no_colours(tmp_path):
    (tmp_path / ".folders.json").write_text("{not json")
    assert FolderColors(tmp_path).all() == {}
    (tmp_path / ".folders.json").write_text(json.dumps({"A": "red", "B": "#0090ff", "C": 3}))
    assert FolderColors(tmp_path).all() == {"B": "#0090ff"}


def test_api(client):
    assert client.post("/api/notes/folder", json={"path": "Projects/Sub"}).status_code == 201
    r = client.put("/api/notes/folder/color", json={"path": "Projects", "color": "#0090ff"})
    assert r.status_code == 200 and r.get_json() == {"folder_colors": {"Projects": "#0090ff"}}
    assert client.get("/api/notes").get_json()["folder_colors"] == {"Projects": "#0090ff"}
    assert client.put("/api/notes/folder/color", json={"path": "Nope", "color": "#0090ff"}).status_code == 400
    assert client.put("/api/notes/folder/color", json={"path": "Projects", "color": "blue"}).status_code == 400
    # the colour follows the folder when it is renamed, and goes when the folder is gone
    assert client.post("/api/notes/folder/move", json={"path": "Projects", "to": "Work"}).status_code == 200
    assert client.get("/api/notes").get_json()["folder_colors"] == {"Work": "#0090ff"}
    assert client.post("/api/notes/trash", json={"paths": [], "folders": ["Work"]}).status_code == 200
    assert client.get("/api/notes").get_json()["folder_colors"] == {}
    r = client.put("/api/notes/folder/color", json={"path": "../x", "color": "#0090ff"})
    assert r.status_code == 400


def test_other_sites_cannot_colour_folders(client):
    client.post("/api/notes/folder", json={"path": "P"})
    r = client.put(
        "/api/notes/folder/color", json={"path": "P", "color": "#0090ff"}, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
