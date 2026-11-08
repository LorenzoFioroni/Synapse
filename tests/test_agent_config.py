"""Shared roles must generate usable, synchronized adapters without changing unrelated settings."""

import json
import shutil

import pytest
import yaml

from tools.agents import ROOT, adapters, sync


@pytest.fixture
def role_repo(tmp_path):
    shutil.copytree(ROOT / ".agents", tmp_path / ".agents")
    return tmp_path


def test_checked_in_adapters_match_shared_roles():
    assert sync(ROOT, check=True) == 0
    for path, content in adapters(ROOT).items():
        if path.suffix == ".md":
            front = yaml.safe_load(content.split("---", 2)[1])
            assert front["name"] == path.stem
            assert front["model"] in {"haiku", "sonnet", "opus"}
        elif path.suffix == ".toml":
            # JSON strings are also TOML basic strings; Python 3.10 has no built-in TOML reader.
            fields = dict(line.split(" = ", 1) for line in content.splitlines() if " = " in line)
            assert json.loads(fields["name"]) == path.stem
            assert json.loads(fields["developer_instructions"]).startswith("Follow AGENTS.md.")


def test_drift_check_does_not_write_and_generation_keeps_other_settings(role_repo):
    settings = role_repo / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"agent": "old", "language": "English"}))
    before = settings.read_text()
    assert sync(role_repo, check=True) == 1
    assert settings.read_text() == before
    assert not (role_repo / ".codex").exists()
    assert sync(role_repo, check=False) == 0
    assert json.loads(settings.read_text()) == {"agent": "clara", "language": "English"}
    assert sync(role_repo, check=True) == 0
    (role_repo / ".agents/roles/brie.md").write_text("A revised briefing role.\n")
    assert sync(role_repo, check=True) == 1
    sync(role_repo, check=False)
    assert "A revised briefing role." in (role_repo / ".claude/agents/brie.md").read_text()


def test_removed_roles_remove_only_owned_adapters(role_repo):
    sync(role_repo, check=False)
    custom = role_repo / ".claude/agents/personal.md"
    custom.write_text("An unrelated manually maintained agent.\n")
    config_path = role_repo / ".agents/config.json"
    config = json.loads(config_path.read_text())
    config["roles"] = [r for r in config["roles"] if r["name"] != "brie"]
    config_path.write_text(json.dumps(config))
    assert sync(role_repo, check=True) == 1
    sync(role_repo, check=False)
    assert not (role_repo / ".claude/agents/brie.md").exists()
    assert custom.exists()


def test_invalid_names_cannot_escape_the_agent_folders(role_repo):
    path = role_repo / ".agents/config.json"
    config = json.loads(path.read_text())
    config["roles"][0]["name"] = "../elsewhere"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="Invalid or duplicate"):
        adapters(role_repo)
